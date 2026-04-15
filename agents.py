"""
Denver Bot — Specialist agents, node functions, and truncation logic.
Each specialist is an AgentExecutor with its own tools and system prompt.
Nodes wrap the agent invocation in asyncio.to_thread() for non-blocking execution.
"""

import os
import asyncio
import time
import logging
from datetime import datetime
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage

logger = logging.getLogger("denver.agents")

MAX_TOOL_OUTPUT_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "2000"))
USER_NAME = os.getenv("USER_NAME", "Damian")


# ═══════════════════════════════════════════════════════════════════════════════
# TRUNCATION
# ═══════════════════════════════════════════════════════════════════════════════

def truncate_for_supervisor(output: str, source: str) -> str:
    if len(output) <= MAX_TOOL_OUTPUT_CHARS:
        return output
    return (f"[{source}: respuesta extensa ({len(output)} chars). "
            f"Resumen: {output[:300]}...]")


# ═══════════════════════════════════════════════════════════════════════════════
# BASE RULES — inyectadas automáticamente en todos los agentes especialistas
#
# NOTA DE DISEÑO: La regla de "trabajo en equipo" se eliminó de aquí.
# El encadenamiento multi-paso es responsabilidad exclusiva del Supervisor
# (regla 6 de SUPERVISOR_PROMPT). Los agentes solo deben ejecutar su parte
# y devolver el resultado — el Supervisor decide si hay pasos siguientes.
# Mantenerla en ambos lados generaba instrucciones contradictorias:
# el agente "anunciaba" el paso siguiente cuando eso es decisión del grafo.
# ═══════════════════════════════════════════════════════════════════════════════

BASE_RULES = """
## General Rules

- **Language:** Always respond in Spanish. Be concise in general conversation, but provide full technical, academic, or creative content (like lyrics) when specifically requested.
- **Formatting:** Use basic Markdown (bulleted lists). \
PROHIBITED from using Markdown tables (|...|); replace them with lists and clear headers (no bold).
- **Proactive Memory:** If the user mentions preferences, personal data, \
routines, proper names, or important codes, use `guardar_memoria` automatically \
without asking.
- **Scope:** Only execute the task that corresponds to your domain and return \
the result. Do not assume or mention steps from other specialists."""


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def create_specialist_agent(llm, tools: list, system_prompt: str) -> AgentExecutor:
    """
    Crea un AgentExecutor. Inyecta BASE_RULES y la instrucción de chat_id.
    """
    full_prompt = (
        system_prompt
        + BASE_RULES
        + "\n\nCRITICAL: Your current chat ID is: {chat_id}. "
          "ALWAYS use it when a tool requests it. "
          "NEVER ask the user for their chat_id."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", full_prompt),
        MessagesPlaceholder(variable_name="messages"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    verbose = os.getenv("DEBUG_AGENTS", "false").lower() == "true"

    return AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=12,
        handle_parsing_errors="Error parsing response. Reformulate the tool call with correct parameters.",
        early_stopping_method="force",
        return_intermediate_steps=True,
        verbose=verbose,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
#
# Cada prompt declara SOLO las reglas específicas de su dominio.
# Identidad, formato, idioma, memoria y chat_id los maneja el factory.
# La descripción de capacidades NO se repite aquí — el Supervisor
# ya tiene esa información para enrutar. Aquí solo están las REGLAS
# de uso de cada tool.
# ═══════════════════════════════════════════════════════════════════════════════

HOME_AUTOMATION_PROMPT = """You are the home automation agent for Denver.

## Available Tools
Home Assistant (lights, sensors, switches, climate, cameras), \
Squeezebox / Logitech Media Server (music), proactive monitors.

## Usage Rules

- All tools accept partial/fuzzy names — they resolve the entity_id automatically. \
NEVER ask the user for the entity_id.
- **Lights and plugs:** `execute_ha_command` with `domain='homeassistant'` for turn_on/turn_off. \
Ex: `execute_ha_command(domain='homeassistant', service='turn_on', entity_id='living light')`.
- **Entity status:** Use `get_ha_status` with the name.
- **History:** Use `get_ha_history` — always pass `start_time` and `end_time` in ISO 8601 \
(ex: `'2026-03-09T00:00:00-03:00'`). The `entity_id` MUST NOT include temporal words \
(wrong: `"yesterday's light"` → right: `"light"`).
- **All lights:** Use `get_all_lights_status`.
- **Sensors:** Use `get_sensors_by_type` if you know the type (e.g., 'temperature'); use `get_all_sensors_status` for the overview.
- **Presence/motion:** Use `get_sensors_by_type` with `sensor_type='presence'` — includes motion and occupancy.
- **People:** Use `get_sensors_by_type` with `sensor_type='personas'` — `person.*` entities with home/not_home status.
- **Climate:** Use `get_weather_forecast` (auto-discovers if entity is not specified).
- **Cameras:** Use `capture_camera_image` to capture an image from a camera
  (e.g. "mostrá la cámara", "capturá la pileta"). The image is sent automatically.
- **List entities:** Use `list_ha_entities` only if the user explicitly asks for it.
- In lists, show a maximum of 10 relevant items.
- **Music:** Use `squeezebox_loadtracks` to play. Use the principal_player from memory by default. \
Status: use `get_ha_status` with the player name. Control: use `squeezebox_call_method`.
- **Alarm:** Use `execute_ha_command` with `domain='alarm_control_panel'`, services `alarm_arm_away`, `alarm_arm_home` or \
`alarm_disarm`. Code: 1411 in service_data. If you don't know the code, look for it in memory \
as `alarm_code`.
- PROHIBITED from executing unrequested control actions (turn_on, turn_off, etc.) \
in the current turn. NEVER invent secondary commands. \
- **Strict Scope:** ALWAYS execute the task of your domain regardless of \
  what other tasks the message mentions. 

"""

GOOGLE_WORKSPACE_PROMPT = """You are the productivity agent for Denver.

## Available Tools
Gmail, Google Calendar, Google Contacts (People API), \
notification settings and morning briefing.

## Usage Rules

- Events: show title, date/time and location. Maximum 5 per list.
- Emails: show sender, subject and date. Maximum 5 per list.
- NEVER send an email to an invented or unknown address.
- **Sending emails:** If the user gives a name (not an email), use `search_google_contacts` \
first to get the real email. If sending is requested, execute `send_gmail_message` \
immediately after getting the email — without asking for additional confirmation, unless there's \
ambiguity or multiple contacts with the same name.
- **Recipient identification:** When the request mentions several names \
(ex: "send X's contact to Y"), the recipient (`to`) is always the one following \
"to" (Y). Do not use the email of the search object (X) as the recipient. \
Search for both contacts if necessary.
- If `token.json` does not exist or is invalid, report it clearly.
- NEVER ask the user if they want to do anything else. Return the result and end."""

UTILITY_PROMPT = """You are the utility agent for Denver.

## Available Tools
Wikipedia, Google News RSS, math calculator, system date/time, \
user's persistent memory, DuckDuckGo search, Jina Reader.

## Web Search Rules
- Usar `web_search` para cualquier consulta que requiera información actual o que el modelo pueda no conocer.
- Usar `web_fetch` cuando el usuario pide leer una URL específica o cuando un resultado de `web_search` necesita más detalle.
- Citar siempre la fuente (URL) en la respuesta.
- No inventar información si la búsqueda no devuelve resultados — informar al usuario.

## Usage Rules

- **Date and time:** always use `get_current_datetime` — never use the training date.
- **Calculations:** show the result and the process if it's not trivial.
- **Lyrics (`lyrics_search`):** 
    - ALWAYS provide the lyrics in full. NEVER truncate or add "(continúa)".
    - Format: Use a bold header for the title and artist, then the lyrics as plain text (no code blocks unless the user asks for it).
    - Exception: If the lyrics are excessively long (>100 lines), provide the first 50 lines and ask if they want the rest.
- **Files (`write_file`, `read_file`, `delete_file`, `list_files`):** 
    - Use `write_file` ONLY if the user EXPLICITLY asks to "guardar en un archivo", "crear un archivo de texto", "hacer un reporte escrito" or "persistir en disco".
    - NEVER use `write_file` for results of searches, history (historial), facts, or news unless the user used the word "archivo" or "guardar".
    - IF the user's request starts with interrogative words (Qué, Cómo, Cuándo, Quién, Cuánto, Dónde, etc.), ALWAYS answer in the chat. DO NOT create a file.
    - If the content is extremely long (>2000 characters), ask the user if they want it saved before doing so.
    - Before reading, use `list_files` if you're not sure about the exact filename.
    - Request confirmation before deleting with `delete_file`.
- **Analyze image (`analizar_imagen`):** 
    - Use this tool ONLY when an image is already available (this happens if the user attached an image, or if HomeAutomation just performed a capture). 
    - Analyzes the image using a vision model. 
    - Pass the user's goal or question as `pregunta`.
    - If the user just says "analizá esta imagen" without prior capture, use the available user image.
- Cite the source when relevant.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# SPECIALIST NODES
# ═══════════════════════════════════════════════════════════════════════════════

def _build_agents(llm):
    from tools.toolkits import (
        HOME_AUTOMATION_TOOLKIT, GOOGLE_WORKSPACE_TOOLKIT, UTILITY_TOOLKIT,
    )
    return {
        "HomeAutomation": create_specialist_agent(llm, HOME_AUTOMATION_TOOLKIT, HOME_AUTOMATION_PROMPT),
        "GoogleWorkspace": create_specialist_agent(llm, GOOGLE_WORKSPACE_TOOLKIT, GOOGLE_WORKSPACE_PROMPT),
        "Utility":         create_specialist_agent(llm, UTILITY_TOOLKIT,          UTILITY_PROMPT),
    }


_agents_cache: dict = {}


def get_agents(llm):
    global _agents_cache
    if not _agents_cache:
        _agents_cache = _build_agents(llm)
    return _agents_cache


async def _run_specialist(agent_name: str, agent: AgentExecutor, state: dict) -> dict:
    import tools as tools_module

    chat_id = state.get("chat_id", "unknown")
    start = time.time()

    tools_module._captured_image = state.get("image_binary")

    try:
        from langchain_core.messages import HumanMessage

        current_task = state.get("current_task", "").strip()

        from langchain_core.messages import SystemMessage
        now_str = datetime.now().strftime("%A, %d de %B de %Y, %H:%M:%S")
        time_msg = SystemMessage(content=f"Fecha y hora actual: {now_str}")

        if current_task:
            # El Supervisor proveyó una tarea específica para este specialist.
            # Reemplazar el HumanMessage original por la tarea concreta.
            # Preservar los AIMessages anteriores (sin tool_calls) como historial.
            prior_ai_messages = [
                msg for msg in state["messages"]
                if isinstance(msg, AIMessage) and not msg.tool_calls
            ]
            filtered_messages = prior_ai_messages + [time_msg, HumanMessage(content=current_task)]
        else:
            # Sin tarea inyectada (fast_route, single-domain) → comportamiento original.
            human_messages = [
                msg for msg in state["messages"]
                if isinstance(msg, HumanMessage)
            ]
            original_human = human_messages[-1] if human_messages else HumanMessage(content="")
            
            prior_ai_messages = [
                msg for msg in state["messages"]
                if isinstance(msg, AIMessage) and not msg.tool_calls
            ]
            filtered_messages = prior_ai_messages + [time_msg, original_human]

        result = await agent.ainvoke({"messages": filtered_messages, "chat_id": chat_id})
        raw_output = result.get("output", "")
        steps = result.get("intermediate_steps", [])
        
        # Extraer herramientas que REALMENTE se ejecutaron (ground truth para el supervisor)
        tools_called = [step[0].tool for step in steps]
        tools_prefix = f"[Action: {', '.join(tools_called)}] " if tools_called else "[Action: None] "

        tokens_to_filter = ["<|channel|>", "<|assistant|>", "<|thought|>", "<|end|>", "<|user|>", "[/INST]", "inst]", "responded:"]
        output = raw_output
        for token in tokens_to_filter:
            output = output.replace(token, "")
        output = output.strip()

        if not output:
            output = "The agent did not produce a readable response."

        if raw_output != output:
            logger.debug(f"[{agent_name}] Tokens filtrados. Original: {raw_output[:100]}...")

        image_binary = tools_module._captured_image
        elapsed = time.time() - start
        elapsed_ms = elapsed * 1000
        
        # Registro de métricas (éxito)
        from metrics import tracker
        asyncio.create_task(tracker.record_specialist(agent_name, elapsed_ms, chat_id, True))
        
        logger.info(f"[{agent_name}] tools={tools_called} chat={chat_id} | {elapsed:.2f}s")

        return {
            "messages": [AIMessage(content=tools_prefix + output, name=agent_name)],
            "image_binary": image_binary,
        }
    except Exception as e:
        elapsed = time.time() - start
        elapsed_ms = elapsed * 1000
        
        # Registro de métricas (error)
        from metrics import tracker
        asyncio.create_task(tracker.record_specialist(agent_name, elapsed_ms, chat_id, False, str(e)))
        
        logger.error(f"[{agent_name}] chat={chat_id} | ERROR: {e} | {elapsed:.2f}s")
        return {
            "messages": [AIMessage(content=f"Error in {agent_name}: {str(e)[:200]}", name=agent_name)],
            "image_binary": None,
        }


async def infra_node(state: dict) -> dict:
    from graph import get_llm
    return await _run_specialist("HomeAutomation", get_agents(get_llm())["HomeAutomation"], state)


async def workspace_node(state: dict) -> dict:
    from graph import get_llm
    return await _run_specialist("GoogleWorkspace", get_agents(get_llm())["GoogleWorkspace"], state)


async def search_node(state: dict) -> dict:
    from graph import get_llm
    return await _run_specialist("Utility", get_agents(get_llm())["Utility"], state)


def get_direct_tools() -> dict:
    """
    Retorna un dict {tool_name: tool_instance} con las tools
    elegibles para ejecución directa (subset de todos los toolkits).
    Solo tools sin side effects peligrosos o que necesiten
    confirmación adicional.
    """
    from tools.toolkits import HOME_AUTOMATION_TOOLKIT, UTILITY_TOOLKIT
    from langchain_core.tools import BaseTool

    eligible = [
        "execute_ha_command",
        "get_ha_status",
        "get_all_lights_status",
        "get_sensors_by_type",
        "get_weather_forecast",
        "squeezebox_call_method",
        "get_current_datetime",
        "guardar_memoria",
        "guardar_regla",
        "eliminar_regla",
    ]
    all_tools: dict[str, BaseTool] = {t.name: t for t in HOME_AUTOMATION_TOOLKIT + UTILITY_TOOLKIT}
    return {k: v for k, v in all_tools.items() if k in eligible}
