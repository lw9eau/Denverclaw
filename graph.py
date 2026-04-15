"""
Denver Bot — Supervisor StateGraph, routing, and memory injection.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal, TypedDict, Optional
from functools import lru_cache

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
from pydantic import BaseModel
from sqlalchemy import select

from db.database import async_session
from db.models import Memoria, MemoriaConfig
from agents import infra_node, workspace_node, search_node, MAX_TOOL_OUTPUT_CHARS
from utils.routing_engine import RoutingEngine, normalize_text as _normalize_text
from metrics import tracker
import time

logger = logging.getLogger("denver.graph")

# ─── Routing Engine ───────────────────────────────────────────────────────────
ROUTING_ENGINE = RoutingEngine()

# ─── Environment ──────────────────────────────────────────────────────────────

MAX_MEMORY_MESSAGES = int(os.getenv("MAX_MEMORY_MESSAGES", "12"))
MAX_DELEGATIONS = int(os.getenv("MAX_DELEGATIONS", "5"))
MEMORIA_PERSISTENTE = os.getenv("MEMORIA_PERSISTENTE", "true").lower() == "true"
USER_NAME = os.getenv("USER_NAME", "Damian")

# ─── Signals ──────────────────────────────────────────────────────────────────
_ERROR_SIGNALS = ["❌", "error", "fallo", "falló", "no pudo", "no pude", "no disponible"]
_CHAIN_SIGNALS = ["falta", "pendiente", "además", "ademas", "luego", "después", "despues"]


def _is_complex_intent(text: str) -> bool:
    """
    Analiza el texto para determinar si parece una intención multi-paso o compleja.
    """
    if not text:
        return False
    
    t = _normalize_text(text)
    
    # 1. Conectores de secuencia o adición
    COMPLEX_CONNECTORS = [" y ", " luego ", " despues ", " después ", " ademas ", " además ", " tambien ", " también "]
    if any(c in t for c in COMPLEX_CONNECTORS):
        return True
    
    # 2. Verbos que suelen implicar una segunda acción (envío, guardado, análisis)
    MULTI_STEP_VERBS = ["manda", "envia", "enviar", "enviá", "guarda", "guardá", "crea", "creá", "analiza", "analizá", "decime", "dime"]
    if any(v in t for v in MULTI_STEP_VERBS):
        return True
        
    # 3. Presencia de múltiples dominios (keywords)
    HA_KEYWORDS = ["luz", "luces", "clima", "temperatura", "camara", "camaras", "captura", "musica"]
    GW_KEYWORDS = ["mail", "correo", "calendario", "evento", "reunion", "agenda"]
    UTIL_KEYWORDS = ["wikipedia", "wiki", "noticias", "calcula", "busca", "googlea", "analiza", "vision", "imagen", "foto", "que ves", "describe"]
    
    ha_match = any(k in t for k in HA_KEYWORDS)
    gw_match = any(k in t for k in GW_KEYWORDS)
    util_match = any(k in t for k in UTIL_KEYWORDS)
    
    if sum([ha_match, gw_match, util_match]) > 1:
        return True
        
    return False


def _safe_auto_finish(
    messages: list,
    delegation_count: int,
    is_new_turn: bool,
) -> bool:
    """
    Retorna True si es seguro hacer FINISH sin llamar al LLM.

    Condiciones TODAS deben cumplirse:
    1. No es turno nuevo (is_new_turn == False)
    2. delegation_count == 1
    3. El último mensaje es un AIMessage de un especialista
    4. El contenido del último mensaje no contiene señales de error
    5. El contenido del último mensaje no contiene señales de continuación
    6. El delegation_count no alcanzó MAX_DELEGATIONS
    """
    try:
        # 1. No es turno nuevo
        if is_new_turn:
            logger.debug("[AutoFinish] Skip: is_new_turn=True")
            return False
        
        # 2. delegation_count == 1
        if delegation_count != 1:
            logger.debug(f"[AutoFinish] Skip: delegation_count={delegation_count}")
            return False

        # 6. Salvaguarda redundante
        if delegation_count >= MAX_DELEGATIONS:
            logger.debug("[AutoFinish] Skip: MAX_DELEGATIONS reached")
            return False

        if not messages:
            return False

        last_msg = messages[-1]
        
        # 3. El último mensaje es un AIMessage de un especialista
        if not isinstance(last_msg, AIMessage):
            logger.debug("[AutoFinish] Skip: last message is not AIMessage")
            return False
            
        specialist_name = getattr(last_msg, "name", None)
        if specialist_name not in ("HomeAutomation", "GoogleWorkspace", "Utility"):
            logger.debug(f"[AutoFinish] Skip: last message owner is {specialist_name}")
            return False

        content_lower = last_msg.content.lower()

        # 4. Señales de error
        for signal in _ERROR_SIGNALS:
            if signal.lower() in content_lower:
                logger.debug(f"[AutoFinish] Skip: error signal detected ('{signal}')")
                return False

        # 5. Señales de continuación
        for signal in _CHAIN_SIGNALS:
            if signal.lower() in content_lower:
                logger.debug(f"[AutoFinish] Skip: chain signal detected ('{signal}')")
                return False

        # 7. Contenido sustancial (evitar respuestas vacías o fallidas del specialist)
        import re
        clean_content = re.sub(r'^\[Action: .*?\]\s*', '', last_msg.content).strip()
        if len(clean_content) < 10:
            logger.debug(f"[AutoFinish] Skip: content too short ({len(clean_content)} chars)")
            return False

        # 8. Intención original compleja
        # Buscar el último mensaje humano (el que inició este turno)
        original_human = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                original_human = msg.content
                break
        
        if original_human and _is_complex_intent(original_human):
            logger.debug(f"[AutoFinish] Skip: complex intent detected in '{original_human}'")
            return False

        logger.debug(f"[AutoFinish] Condiciones OK: delegation={delegation_count}, specialist={specialist_name}")
        return True

    except Exception as e:
        logger.warning(f"[AutoFinish] Exception in _safe_auto_finish: {e}")
        return False



@lru_cache(maxsize=8)
def get_llm(temperature: float = 0) -> ChatOpenAI:
    url = os.getenv("LLM_URL", "http://localhost:1234/v1")
    model = os.getenv("LLM_MODEL", "gpt-oss:120b-cloud")
    api_key = os.getenv("LLM_API_KEY", "lm-studio")
    logger.debug(f"[get_llm] new ChatOpenAI instance (temperature={temperature})")
    return ChatOpenAI(base_url=url, model=model, api_key=api_key, temperature=temperature)


# ─── State ────────────────────────────────────────────────────────────────────

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    chat_id: str
    is_voice: bool
    image_binary: bytes | None
    next: str
    delegation_count: int
    current_task: str       # tarea específica para el próximo specialist
    interface: str          # 'telegram', 'voice', 'web', etc.
    rules_text: str         # reglas dinámicas inyectadas desde memoria
    memory_facts_text: str  # hechos de memoria cacheados por turno (evita N queries a DB)


# ─── Router ───────────────────────────────────────────────────────────────────

class RouterResponse(BaseModel):
    next: Literal["HomeAutomation", "GoogleWorkspace", "Utility", "FINISH", "DIRECT"]
    reasoning: str
    task: str = ""        # NUEVO: instrucción concreta para el specialist
    tool_name: Optional[str] = None       # nombre de la tool a ejecutar directo
    tool_args: Optional[dict] = None      # argumentos de la tool



# (Logic moved to utils/routing_engine.py)


def _direct_tool_route(text: str, normalized: str = None) -> RouterResponse | None:
    """
    Ruteo determinístico para comandos de una sola acción.
    Si detecta un patrón conocido, retorna next='DIRECT' y los args de la tool.
    """
    res = ROUTING_ENGINE.route(text)
    if res:
        return RouterResponse(
            next="DIRECT",
            reasoning=f"DirectRoute: {res['name']}",
            tool_name=res["tool_name"],
            tool_args=res["tool_args"]
        )
    return None


async def _execute_direct_tool(
    tool_name: str,
    tool_args: dict,
    chat_id: str,
    all_tools: dict
) -> dict:
    """
    Ejecuta una tool directamente sin pasar por un AgentExecutor.
    Retorna un dict con la misma forma que los specialist nodes.
    """
    from langchain_core.messages import AIMessage
    
    tool = all_tools.get(tool_name)
    if not tool:
        return {
            "messages": [AIMessage(content=f"Error: Tool '{tool_name}' no disponible.", name="DirectTool")],
            "image_binary": None
        }

    # Inyectar chat_id si la tool lo requiere (LangChain tool.args contiene los parámetros)
    if "chat_id" in tool.args:
        tool_args["chat_id"] = chat_id

    try:
        if hasattr(tool, "ainvoke"):
            output = await tool.ainvoke(tool_args)
        else:
            import asyncio
            output = await asyncio.to_thread(tool.invoke, tool_args)
        
        # El output de BaseTool suele ser un string
        content = str(output)
        
        return {
            "messages": [AIMessage(content=content, name="DirectTool")],
            "image_binary": None # Direct tools no capturan imágenes por ahora (o lo manejan via side channel)
        }
    except Exception as e:
        logger.error(f"[DirectTool] Error ejecutando {tool_name}: {e}")
        return {
            "messages": [AIMessage(content=f"Error ejecutando {tool_name}: {str(e)}", name="DirectTool")],
            "image_binary": None
        }


# ─── Fast Router (Pure Python) ────────────────────────────────────────────────

def _fast_route(text: str, normalized: str = None) -> RouterResponse | None:
    """
    Determina el agente mediante coincidencia de palabras clave en Python puro.
    Cubre ~60% de los casos comunes con 0ms de latencia.

    Retorna None en dos situaciones:
      - El texto no matchea ningún dominio conocido
      - El texto matchea keywords de MÁS DE UN agente (multi-paso)
        → en ese caso el LLM supervisor decide el encadenamiento
    """
    if not text:
        return None

    # Use pre-normalized text if provided, otherwise normalize now
    t = normalized if normalized is not None else _normalize_text(text)

    # ── Saludos y charla — FINISH directo ─────────────────────────────────────
    # Solo si el mensaje es CORTO (< 40 chars) para no capturar frases largas
    # que empiecen con "hola" pero luego pidan algo concreto.
    GREETINGS = ["hola", "chau", "gracias", "quien sos", "que haces",
                 "buen dia", "buenas tardes", "buenas noches", "como estas",
                 "que tal", "todo bien"]
    if len(t) < 40 and any(k in t for k in GREETINGS):
        return RouterResponse(next="FINISH", reasoning="FastRoute: Greeting or small talk.")

    # ── Keywords por dominio ───────────────────────────────────────────────────
    HA_KEYWORDS = [
        "luz", "luces", "prende", "apaga", "enciende", "encender", "apagar",
        "clima", "temperatura", "lluvia", "pronostico",
        "musica", "cancion", "reproducir", "pausar", "volumen", "siguiente", "play",
        "camara", "camaras", "captura", "foto",
        "entrepiso", "living", "fondo", "galpon", "galeria", "pileta", "frente",
        "alarma", "sensor", "sensores", "movimiento", "presencia",
        "persiana", "ventilador", "calefaccion", "aire", "porton",
    ]

    GW_KEYWORDS = [
        "mail", "correo", "email", "manda", "envia", "enviar",
        "calendario", "evento", "reunion", "agenda", "cita",
        "contacto", "telefono", "celular", "gmail",
        "notificacion", "resumen", "briefing",
        "que tengo", "que hay", "mi dia", "mi agenda", "tengo para hoy",
    ]

    UTIL_KEYWORDS = [
        "wikipedia","wiki", "noticias", "calcula", "cuanto es", "cuanto son",
        "que hora", "que fecha", "que dia", "hoy es",
        "recorda", "memoria", "guarda",
        "archivo", "escribi", "lee", "borra", "lista",
        "busca", "busca", "buscar", "googlea", "googlear", "que es", "quien es",
        "cuanto vale", "precio de", "noticias de", "lee esta pagina",
        "abri este link", "http", "https", "www",
        "analiza", "analizar", "vision", "imagen", "foto", "que ves", "viste", "describe", "describime",
    ]

    ha_match   = any(k in t for k in HA_KEYWORDS)
    gw_match   = any(k in t for k in GW_KEYWORDS)
    util_match = any(k in t for k in UTIL_KEYWORDS)

    matched = sum([ha_match, gw_match, util_match])

    # ── Multi-agente → LLM supervisor ─────────────────────────────────────────
    if matched > 1:
        return None  # ej: "mandá un mail con el estado de las luces"

    # ── Match único → agente directo ──────────────────────────────────────────
    if ha_match:
        return RouterResponse(next="HomeAutomation",
                              reasoning="FastRoute: home automation keyword.")
    if gw_match:
        return RouterResponse(next="GoogleWorkspace",
                              reasoning="FastRoute: Workspace keyword.")
    if util_match:
        return RouterResponse(next="Utility",
                              reasoning="FastRoute: utility keyword.")

    # Sin match → LLM supervisor
    return None


# ─── Supervisor system prompt ─────────────────────────────────────────────────
#
# CAMBIOS vs versión anterior:
# - Eliminada la descripción de capacidades por especialista: esa info ya vive
#   en cada agente. Aquí solo se mantienen las REGLAS DE RUTEO puras.
# - La regla de "trabajo en equipo / encadenamiento" se deja únicamente acá
#   (era redundante en los 3 agentes; se eliminó de agents.py/BASE_RULES).
# - La identidad "Denver / asistente personal de {USER_NAME}" se declara una
#   sola vez, aquí, como fuente de verdad.
# - La regla de clima se reformuló de forma más corta y precisa.
# - Se añade contexto explícito sobre qué hacer cuando llega una imagen.

SUPERVISOR_PROMPT = f"""You are the central coordinator of Denver, personal assistant of {USER_NAME}.
Your job is to decide which specialist handles the NEXT step, and to write the EXACT instruction for that specialist.

## Specialists and their exclusive domain

| Specialist      | Domain                                                                   |
|-----------------|--------------------------------------------------------------------------|
| HomeAutomation  | Lights, plugs, sensors, history/logs, climate/forecast, cameras, music (Squeezebox) |
| GoogleWorkspace | Gmail, Google Calendar, contacts, notifications, morning briefing        |
| Utility         | vision/image analysis, Wikipedia, news, calculations/math, date/time, web search, persistent memory, file system (write/read/delete/list files) |

## Response format

Respond with this JSON format.
```json
{{"next": "SpecialistName", "task": "Exact instruction for the specialist.", "reasoning": "..."}}
```

## Rules

1. **`task` field is mandatory** when `next` is a specialist. Write a single, self-contained instruction in Spanish. Include only what that specialist needs — nothing about what other specialists will do before or after.
2. **Isolate each task.** If the user asked "capture the pool camera and email it to Juan":
   - First delegation: `{{"next": "HomeAutomation", "task": "Capturá la imagen de la cámara pileta.", "reasoning": "..."}}`
   - Second delegation (after HA responds): `{{"next": "GoogleWorkspace", "task": "Enviá la imagen capturada por mail a Juan.", "reasoning": "..."}}`
   Never include "and send it by email" in the HomeAutomation task.
   Never include "capture the camera" in the GoogleWorkspace task.
3. **FINISH** when the full original request is satisfied. Use `{{"next": "FINISH", "task": "", "reasoning": "..."}}`.
4. **Climate always to HomeAutomation** — never to Utility.
5. **Files and Reports** — ONLY if the user EXPLICITLY asks to "save to a file", "create a file", or "write a report" (e.g., using words like "archivo", "guardar", "reporte"), delegate the final step to **Utility**.
   - **CRITICAL:** Do NOT create files for history/logs, news, facts, or any question starting with who/when/what. These must ALWAYS result in a chat response (FINISH).
   - Results for "log" or "historial" should be delivered as text in the chat, NOT as a file, unless explicitly requested.
6. **Camera capture + analysis** — If the user asks to capture a camera AND analyze it
   (e.g., "capture the backyard camera and tell me if someone is there"),
   you MUST split it into TWO separate sequential tasks:
   - First delegation: `{{"next": "HomeAutomation", "task": "Capturá la cámara del fondo usando capture_camera_image.", "reasoning": "..."}}`
   - Second delegation (after capture is successful): `{{"next": "Utility", "task": "Analizá la imagen recién capturada usando analizar_imagen respondiendo a la pregunta: ¿hay alguien?.", "reasoning": "..."}}`
   Do NOT ask to analyze before the image is successfully captured.
7. **Multi-step chaining** — check the history to identify who already acted successfully.
   Delegate to the NEXT needed specialist with its specific task. Never re-delegate to the last specialist.
8. **Greetings and small talk** — `{{"next": "FINISH", "task": "", "reasoning": "..."}}`. You respond directly.
9. **Specialist error** — do not re-delegate to the same specialist. Try another if applicable, or FINISH with the error.
10. **Anti-loop** — if `delegation_count` ≥ {MAX_DELEGATIONS}, always FINISH.
11. **DirectTool result** — if the last message name == "DirectTool", always FINISH.
12. **Trust [Action: ...] prefix** — the bracketed prefix identifies real tools executed. Ignore any specialist text that contradicts it.
13. **Entity specificity** — when the user asks for the status or action of a specific device or entity (e.g., 'light in the living room'), the `task` MUST explicitly mention that entity name.
14. **Agenda and Today's tasks** — Queries about the agenda, today's schedule, or daily briefings ALWAYS belong to GoogleWorkspace (Calendar/Gmail). Do NOT confuse with HomeAutomation (weather).
15. **No proactive tasks** — Do NOT create calendar events, reminders, files, or send emails (e.g., about the weather, a fact, or a history log) unless explicitly requested; just answer the question in the chat.
16. **Interface Context** — The `interface` field ('telegram', 'voice', 'web') tells you the capabilities of the current UI. 
    If `interface` is 'web', you ARE encouraged to use extended Markdown, including tables and code blocks.
    If `interface` is 'voice', you MUST keep responses very short and clean of any markdown or symbols.
17. **Dynamic User Rules** — If a section `## Dynamic User Rules` is present, those instructions take precedence over any domestic default. Follow them strictly.
"""


def _parse_router_response(raw_text: str) -> RouterResponse:
    """
    Parse RouterResponse JSON from LLM text output.
    Handles: raw JSON, markdown code blocks, embedded JSON in text.
    """
    import json as json_mod
    import re as re_mod

    try:
        data = json_mod.loads(raw_text)
        return RouterResponse(**data)
    except (json_mod.JSONDecodeError, Exception):
        pass

    code_match = re_mod.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_text, re_mod.DOTALL)
    if code_match:
        try:
            data = json_mod.loads(code_match.group(1))
            return RouterResponse(**data)
        except Exception:
            pass

    json_match = re_mod.search(r'\{[^{}]*"next"\s*:\s*"[^"]*"[^{}]*\}', raw_text)
    if json_match:
        try:
            data = json_mod.loads(json_match.group(0))
            return RouterResponse(**data)
        except Exception:
            pass

    text_upper = raw_text.upper()
    if any(k in text_upper for k in ["FINISH", "TERMINAR", "FINALIZAR"]):
        return RouterResponse(next="FINISH", reasoning=f"Detected 'FINISH' in response: {raw_text[:50]}", task="")

    for agent in ("HomeAutomation", "GoogleWorkspace", "Utility"):
        if agent.upper() in text_upper:
            return RouterResponse(next=agent, reasoning=f"Detected '{agent}' in response text", task="")

    logger.warning(f"[Supervisor] Could not parse response. Raw: {raw_text[:1000]}")
    return RouterResponse(next="FINISH", reasoning="Could not parse response, defaulting to FINISH", task="")


# ─── Supervisor Node ──────────────────────────────────────────────────────────

async def supervisor_node(state: SupervisorState) -> dict:
    turn_start = time.time()
    chat_id = state.get("chat_id", "unknown")
    interface = state.get("interface", "unknown")
    messages = list(state.get("messages", []))
    rules_text = state.get("rules_text", "")

    # Extraer user_text del último mensaje humano para observabilidad
    user_text_for_metrics = ""
    if messages:
        last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        if last_human and isinstance(last_human.content, str):
            if not last_human.content.startswith("data:"):
                user_text_for_metrics = last_human.content[:500].strip()

    # 0. Deterministic intent check (DirectTool or FastRoute) — 0 LLM calls
    is_new_turn = messages and isinstance(messages[-1], HumanMessage)
    if is_new_turn:
        user_text = messages[-1].content
        # Normalize once for both routing functions
        normalized_text = _normalize_text(user_text)

        # 0.5 Image Route — user sent a photo (Telegram / Web Chat)
        # image_binary is injected by handle_photo / websocket before invoking the graph.
        # The supervisor has no other way to know about it, so we force HomeAutomation here.
        if state.get("image_binary") is not None:
            pregunta = user_text.strip() or "Describí detalladamente lo que ves en esta imagen."
            task = f"El usuario envió una imagen directamente. Analizala con analizar_imagen usando pregunta='{pregunta}'."
            logger.info(f"[ImageRoute] chat={chat_id} → Utility (imagen adjunta del usuario)")
            return {
                "next": "Utility",
                "delegation_count": 1,
                "current_task": task,
                "rules_text": rules_text,
            }

        # 1. Direct tool path (High priority, deterministic) — 0 LLM calls additional
        direct_res = _direct_tool_route(user_text, normalized=normalized_text)
        if direct_res and direct_res.next == "DIRECT":
            latency_total = (time.time() - turn_start) * 1000
            asyncio.create_task(tracker.record_route("direct", direct_res.tool_name or "unknown", latency_total, chat_id, interface))
            
            logger.info(
                f"[DirectRoute] chat={chat_id} → {direct_res.tool_name}({direct_res.tool_args})"
            )
            from agents import get_direct_tools
            direct_tools = get_direct_tools()
            result = await _execute_direct_tool(
                direct_res.tool_name,
                direct_res.tool_args or {},
                chat_id,
                direct_tools,
            )
            
            # Registrar turno para rutas directas
            asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, 1, user_text=user_text_for_metrics))
            
            return {
                **result,
                "next": "FINISH",
                "delegation_count": 1,
                "rules_text": rules_text,
            }

        # 2. Fast Route check (Medium priority, keyword based) — 0/1 LLM calls
        fast_res = _fast_route(user_text, normalized=normalized_text)
        if fast_res:
            latency = (time.time() - turn_start) * 1000
            asyncio.create_task(tracker.record_route("fast", fast_res.next, latency, chat_id, interface))
            
            logger.info(f"[FastRoute] chat={chat_id} → {fast_res.next}")
            # Si es FINISH, necesitamos generar la respuesta conversacional
            if fast_res.next == "FINISH":
                # Reutilizamos la lógica de respuesta cálida
                chat_llm = get_llm(temperature=0.0)
                chat_response = await chat_llm.ainvoke([
                    SystemMessage(content=f"You are Denver, personal assistant of {USER_NAME}. Respond in Spanish, in a warm and concise way."),
                    messages[-1]
                ], config={"tags": ["supervisor_chat"]})
                
                latency_total = (time.time() - turn_start) * 1000
                asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, 1, user_text=user_text_for_metrics))

                return {
                    "messages": [AIMessage(content=chat_response.content, name="Supervisor")],
                    "next": "FINISH",
                    "delegation_count": 1,
                }
            
            return {"next": fast_res.next, "delegation_count": 1, "current_task": "", "rules_text": rules_text}

    # 1. Memory injection (cacheada por turno)
    memory_messages = []
    # Leer caché del estado — solo vacío en el primer turno de la conversación
    memory_facts_text = state.get("memory_facts_text", "")

    if is_new_turn and MEMORIA_PERSISTENTE:
        # Primera invocación del turno → consultar DB y almacenar en estado
        try:
            async with async_session() as session:
                config_result = await session.execute(
                    select(MemoriaConfig).where(MemoriaConfig.chat_id == chat_id)
                )
                config = config_result.scalar_one_or_none()
                is_user_active = config.activa if config else True

                if is_user_active:
                    mem_result = await session.execute(
                        select(Memoria).where(Memoria.chat_id == chat_id)
                    )
                    memorias = mem_result.scalars().all()

                    if memorias:
                        facts = []
                        rules = []
                        for m in memorias:
                            if m.clave.startswith("regla_"):
                                rules.append(f"- {m.valor}")
                            else:
                                facts.append(f"- {m.clave}: {m.valor} ({m.descripcion})" if m.descripcion else f"- {m.clave}: {m.valor}")

                        if facts:
                            # Guardar en variable local; se persistirá vía estado en el return
                            memory_facts_text = f"Contexto persistente de {USER_NAME}:\n" + "\n".join(facts)

                        if rules:
                            rules_text = "## Dynamic User Rules\n" + "\n".join(rules)

                        logger.info(f"[Supervisor] memoria inyectada: {len(facts)} facts, {len(rules)} rules para chat={chat_id}")
        except Exception as e:
            logger.warning(f"[Supervisor] Error inyectando memoria: {e}")
    elif memory_facts_text:
        # Invocaciones siguientes del mismo turno (post-delegación) → cache hit, 0 queries a DB
        logger.debug(f"[Supervisor] memory cache hit — skipping DB query para chat={chat_id}")

    # Reconstruir memory_messages desde texto (fresco o cacheado)
    if memory_facts_text:
        memory_messages.append(SystemMessage(content=memory_facts_text))
    if rules_text:
        memory_messages.append(SystemMessage(content=rules_text))

    # 2. Delegation count & Anti-loop
    current_count = 0 if is_new_turn else state.get("delegation_count", 0)

    if current_count >= MAX_DELEGATIONS:
        logger.warning(f"[Supervisor] chat={chat_id} — MAX_DELEGATIONS reached, forcing FINISH")
        latency_total = (time.time() - turn_start) * 1000
        asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, current_count, user_text=user_text_for_metrics))
        return {"next": "FINISH", "delegation_count": current_count, "rules_text": rules_text, "memory_facts_text": memory_facts_text}

    # AutoFinish: evitar segunda llamada LLM cuando el especialista
    # completó exitosamente una tarea de un solo paso
    if _safe_auto_finish(messages, current_count, is_new_turn):
        logger.info(
            f"[AutoFinish] chat={chat_id} | "
            f"delegation={current_count} → FINISH sin LLM"
        )
        latency_total = (time.time() - turn_start) * 1000
        asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, current_count + 1, user_text=user_text_for_metrics))
        
        return {
            "next": "FINISH",
            "delegation_count": current_count + 1,
            "rules_text": rules_text,
            "memory_facts_text": memory_facts_text,
        }

    last_specialist = None
    if not is_new_turn and messages:
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage) and getattr(last_msg, "name", None):
            last_specialist = last_msg.name

    # 3. Trim history to last MAX_MEMORY_MESSAGES
    # Solo tomamos los últimos N mensajes para no saturar el contexto
    trimmed_messages = messages[-MAX_MEMORY_MESSAGES:] if len(messages) > MAX_MEMORY_MESSAGES else messages

    annotated_messages = []
    for msg in trimmed_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "name", None):
            content = msg.content
            if len(content) > MAX_TOOL_OUTPUT_CHARS:
                content = content[:MAX_TOOL_OUTPUT_CHARS] + f"\n\n... [{msg.name}: long response, truncated] ..."
            # Quitamos 'name' del constructor ya que algunos proveedores (Ollama/Llama 3)
            # pueden fallar o ignorarlo. El nombre ya va en el content.
            annotated_messages.append(AIMessage(content=f"[{msg.name}] {content}"))
        else:
            annotated_messages.append(msg)

    now_str = datetime.now().strftime("%A, %d de %B de %Y, %H:%M:%S")
    time_msg = SystemMessage(content=f"Fecha y hora actual: {now_str}")

    full_messages = (
        [SystemMessage(content=SUPERVISOR_PROMPT)]
        + [time_msg]
        + memory_messages
        + annotated_messages
    )

    # Si el último mensaje es de un especialista (AIMessage), añadimos un pequeño nudge
    # para que el LLM Supervisor sepa que debe tomar la decisión del siguiente paso.
    # Algunos modelos (Ollama/Llama 3) pueden devolver vacío si el último mensaje es 'assistant'.
    if full_messages and isinstance(full_messages[-1], AIMessage):
        full_messages.append(HumanMessage(content="Decide el siguiente paso basándote en el resultado anterior."))

    try:
        # Aumentamos levemente la temperatura para evitar silencios deterministas
        llm = get_llm(temperature=0.0)
        llm_start = time.time()
        ai_response = await llm.ainvoke(full_messages)
        llm_latency = (time.time() - llm_start) * 1000
        
        original_raw = ai_response.content
        raw_text = original_raw.strip()

        tokens_to_filter = ["<|channel|>", "<|assistant|>", "<|thought|>", "<|end|>", "<|user|>", "[/INST]", "inst]", "responded:"]
        for token in tokens_to_filter:
            raw_text = raw_text.replace(token, "")
        raw_text = raw_text.strip()

        if not raw_text:
            logger.warning(f"[Supervisor] LLM returned EMPTY response (after filtering tokens). Original raw: {original_raw}")

        try:
            response = _parse_router_response(raw_text)
        except Exception:
            logger.warning(f"[Supervisor] Parsing failure. FULL RAW: {original_raw}")
            raise

        asyncio.create_task(tracker.record_route("llm", response.next, llm_latency, chat_id, interface))
        logger.info(f"[Supervisor] chat={chat_id} → {response.next} | {response.reasoning[:500]}")

        if response.next != "FINISH" and last_specialist and response.next == last_specialist:
            logger.info(f"[Supervisor] chat={chat_id} — preventing re-delegation to {last_specialist}, forcing FINISH")
            response = RouterResponse(next="FINISH", reasoning="Prevented re-delegation to same specialist", task="")

        if response.next == "FINISH" and is_new_turn:
            # Filtrar tool_calls antes de pasarle el historial al LLM conversacional
            filtered_messages = [
                msg for msg in messages
                if not (isinstance(msg, AIMessage) and msg.tool_calls)
            ]
            chat_llm = get_llm(temperature=0.0)
            chat_messages = (
                [SystemMessage(content=(
                    f"You are Denver, personal assistant of {USER_NAME}. "
                    "Respond in Spanish, in a warm and concise way."
                ))]
                + memory_messages
                + filtered_messages
            )
            chat_response = await chat_llm.ainvoke(chat_messages, config={"tags": ["supervisor_chat"]})
            
            latency_total = (time.time() - turn_start) * 1000
            asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, current_count + 1, user_text=user_text_for_metrics))
            
            return {
                "messages": [AIMessage(content=chat_response.content, name="Supervisor")],
                "next": "FINISH",
                "delegation_count": current_count + 1,
                "rules_text": rules_text,
                "memory_facts_text": memory_facts_text,
            }

        if response.next == "FINISH":
            latency_total = (time.time() - turn_start) * 1000
            asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, current_count + 1, user_text=user_text_for_metrics))

        return {
            "next": response.next,
            "delegation_count": current_count + 1,
            "current_task": response.task if response.next not in ("FINISH", "DIRECT") else "",
            "rules_text": rules_text,
            "memory_facts_text": memory_facts_text,
        }

    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"[Supervisor] chat={chat_id} | {error_type}: {e}")
        
        latency_total = (time.time() - turn_start) * 1000
        asyncio.create_task(tracker.record_turn(chat_id, interface, latency_total, current_count + 1, user_text=user_text_for_metrics))

        return {
            "messages": [AIMessage(
                content=f"There was an error processing your request: {str(e)[:150]}",
                name="Supervisor",
            )],
            "next": "FINISH",
            "delegation_count": current_count + 1,
            "rules_text": rules_text,
            "memory_facts_text": memory_facts_text,
        }


# ─── Routing ──────────────────────────────────────────────────────────────────

def route_supervisor(state: SupervisorState) -> str:
    mapping = {
        "HomeAutomation": "infra_node",
        "GoogleWorkspace": "workspace_node",
        "Utility": "search_node",
        "FINISH": END,
    }
    return mapping.get(state.get("next", "FINISH"), END)


# ─── Graph Builder ────────────────────────────────────────────────────────────

_BUILD_GRAPH_LOCK = asyncio.Lock()
_CACHED_GRAPH = None

async def build_graph():
    global _CACHED_GRAPH
    async with _BUILD_GRAPH_LOCK:
        if _CACHED_GRAPH is not None:
            return _CACHED_GRAPH
            
        graph = StateGraph(SupervisorState)

        graph.add_node("supervisor_node", supervisor_node)
        graph.add_node("infra_node", infra_node)
        graph.add_node("workspace_node", workspace_node)
        graph.add_node("search_node", search_node)

        graph.set_entry_point("supervisor_node")

        graph.add_conditional_edges(
            "supervisor_node",
            route_supervisor,
            {
                "infra_node": "infra_node",
                "workspace_node": "workspace_node",
                "search_node": "search_node",
                END: END,
            },
        )

        graph.add_edge("infra_node", "supervisor_node")
        graph.add_edge("workspace_node", "supervisor_node")
        graph.add_edge("search_node", "supervisor_node")

        conn = await aiosqlite.connect("checkpoints.sqlite")
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        _CACHED_GRAPH = graph.compile(checkpointer=checkpointer)
        return _CACHED_GRAPH