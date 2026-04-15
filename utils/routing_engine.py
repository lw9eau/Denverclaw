import re
import unicodedata
from typing import Optional, List, Callable, Dict, Any

def normalize_text(text: str) -> str:
    """Normalize text: lowercase, strip, remove accents."""
    if not text:
        return ""
    t = text.lower().strip()
    t = unicodedata.normalize('NFD', t)
    return "".join(c for c in t if unicodedata.category(c) != 'Mn')

class DirectIntent:
    def __init__(
        self,
        name: str,
        patterns: List[str],
        tool_name: str,
        arg_mapper: Callable[[str, str], Optional[Dict[str, Any]]],
        negative_patterns: List[str] = None,
        strict: bool = False
    ):
        self.name = name
        self.patterns = [normalize_text(p) for p in patterns]
        self.tool_name = tool_name
        self.arg_mapper = arg_mapper
        self.negative_patterns = [normalize_text(p) for p in negative_patterns] if negative_patterns else []
        self.strict = strict

    def match_and_map(self, text: str, normalized: str) -> Optional[Dict[str, Any]]:
        # 1. Check triggers
        if self.strict:
            # En modo estricto, el texto normalizado debe ser IGUAL a uno de los patrones
            if normalized not in self.patterns:
                return None
        else:
            # En modo normal, basta con que el patrón esté contenido
            if not any(p in normalized for p in self.patterns):
                return None
        
        # 2. Check negative constraints
        if any(p in normalized for p in self.negative_patterns):
            return None
            
        # 3. Call mapper
        args = self.arg_mapper(text, normalized)
        if args is None:
            return None
            
        return {
            "tool_name": self.tool_name,
            "tool_args": args,
            "name": self.name
        }

# --- Shared Mappers ---

def extract_ha_hint(text: str, command_keywords: List[str], domain_keywords: List[str]) -> str:
    """
    Inteligencia mejorada para extraer el hint de entidad.
    Busca el comando y el dominio, y toma lo que sobra como el nombre del dispositivo.
    """
    t = normalize_text(text)
    
    # Encontrar la posición más temprana de un comando
    best_cmd_pos = -1
    best_cmd_len = 0
    for ck in command_keywords:
        ck_n = normalize_text(ck)
        pos = t.find(ck_n)
        if pos != -1 and (best_cmd_pos == -1 or pos < best_cmd_pos):
            best_cmd_pos = pos
            best_cmd_len = len(ck_n)
            
    if best_cmd_pos == -1:
        return t # fallback
        
    # El resto de la cadena después del comando es el candidato a entidad
    remainder = t[best_cmd_pos + best_cmd_len:].strip()
    
    # Limpiar conectores y dominios al principio (con espacios para límites de palabra)
    # No usamos normalize_text directamente en los prefijos porque strip() quitaría el espacio
    raw_prefixes = ["de ", "del ", "la ", "el ", "las ", "los ", "que "] + [d + " " for d in domain_keywords]
    
    changed = True
    while changed:
        changed = False
        for pre in raw_prefixes:
            # Normalizar individualmente conservando el espacio
            pre_norm = "".join(c for c in unicodedata.normalize('NFD', pre.lower()) if unicodedata.category(c) != 'Mn')
            if remainder.startswith(pre_norm):
                remainder = remainder[len(pre_norm):].strip()
                changed = True
                break
            
    return remainder if remainder else "luz" # fallback

# --- Intent Registry ---

def get_intents() -> List[DirectIntent]:
    intents = []


    # --- Musica (Alta prioridad para evitar colisiones con Verbos genéricos) ---
    # 8. Música: Pausa
    intents.append(DirectIntent(
        name="HA: Music Pause",
        patterns=["para la musica"],
        tool_name="squeezebox_call_method",
        arg_mapper=lambda t, n: {"entity_id": "musica", "command": "pause", "parameters": []},
        strict=True
    ))
    
    # 9. Música: Siguiente
    intents.append(DirectIntent(
        name="HA: Music Next",
        patterns=["siguiente cancion"],
        tool_name="squeezebox_call_method",
        arg_mapper=lambda t, n: {"entity_id": "musica", "command": "playlist", "parameters": ["index", "+1"]},
        strict=True
    ))
    
    # 10. Música: Play / Reanudar
    intents.append(DirectIntent(
        name="HA: Music Play",
        patterns=["pone play", "poné play"],
        tool_name="squeezebox_call_method",
        arg_mapper=lambda t, n: {"entity_id": "musica", "command": "play", "parameters": []},
        strict=True
    ))

    # 3. Luces: Apagar
    def lights_off_mapper(t, n):
        # Permitir sin la palabra "luz" si el comando es muy claro
        if "luz" in n or "luces" in n or "velador" in n or "lampara" in n or "foco" in n:
            return {
                "domain": "homeassistant", 
                "service": "turn_off", 
                "entity_id": extract_ha_hint(t, ["apaga", "apagar", "apague", "desconecta", "desconectar", "corta", "cortar", "quita", "quitar", "off"], ["luz", "luces", "velador", "lampara", "foco"])
            }
        # Si es un comando de "apagar" genérico pero NO menciona luz, podría ser un switch o media_player. 
        if len(n) < 20: 
             return {
                "domain": "homeassistant", 
                "service": "turn_off", 
                "entity_id": extract_ha_hint(t, ["apaga", "apagar", "apague"], [])
            }
        return None

    intents.append(DirectIntent(
        name="HA: Lights Off",
        patterns=["apaga", "apagar", "apague", "desconecta", "desconectar", "corta", "cortar", "quita", "quitar", "off"],
        negative_patterns=[" sensor", " clima"],
        tool_name="execute_ha_command",
        arg_mapper=lights_off_mapper
    ))
    
    # 4. Luces: Encender
    def lights_on_mapper(t, n):
        if "luz" in n or "luces" in n or "velador" in n or "lampara" in n or "foco" in n:
            return {
                "domain": "homeassistant", 
                "service": "turn_on", 
                "entity_id": extract_ha_hint(t, ["prende", "prender", "prendas", "enciende", "encender", "encende", "conecta", "conectar", "pone", "poner", "on"], ["luz", "luces", "velador", "lampara", "foco"])
            }
        if len(n) < 20:
            return {
                "domain": "homeassistant", 
                "service": "turn_on", 
                "entity_id": extract_ha_hint(t, ["prende", "prender", "enciende", "encender"], [])
            }
        return None

    intents.append(DirectIntent(
        name="HA: Lights On",
        patterns=["prende", "prender", "prendas", "enciende", "encender", "encende", "conecta", "conectar", "pone", "poner", "on"],
        negative_patterns=[" sensor", " clima"],
        tool_name="execute_ha_command",
        arg_mapper=lights_on_mapper
    ))
    
    # 5. Fecha y Hora
    intents.append(DirectIntent(
        name="Utility: DateTime",
        patterns=["que hora es", "que fecha es", "que dia es", "que hora tenes", "que dia es hoy"],
        tool_name="get_current_datetime",
        arg_mapper=lambda t, n: {},
        strict=True
    ))

    return intents

class RoutingEngine:
    # Palabras que sugieren que el Supervisor (LLM) debe analizar el contexto
    COMPLEX_REGEX = [
        # Comunicación/Productividad
        r"\bmail\b", r"\bcorreo\b", r"\bemail\b", r"\bmanda\b", r"\benvia\b", r"\benviar\b", 
        r"\bescribi\b", r"\banota\b", r"\bredacta\b", r"\bnotifica\b", r"\bavisa\b", r"\bgmail\b",
        
        # Archivos/Reportes/Listas
        r"\barchivo\b", r"\bcrear?\b", r"\breporte\b", r"\bnota\b", r"\bdocumento\b", r"\bpdf\b", r"\btxt\b",
        r"\bdetalle\b", r"\blistame\b", r"\bmostrame todo\b", r"\bresumen\b", r"\bresumi\b", r"\bcontame\b",
        
        # Análisis y Comparación
        r"\bmas\b", r"\bmenos\b", r"\bcual\b", r"\bcomo\b", r"\bpromedio\b", r"\bmedia\b", r"\bmayor\b", r"\bmenor\b", 
        r"\bmejor\b", r"\bpeor\b", r"\bporque\b", r"\bpor que\b", r"\bcompara\b", r"\bdiferencia\b", r"\banaliza\b",
        
        # Temporalidad compleja
        r"\bayer\b", r"\bhoy\b", r"\bcuando\b", r"\bcuanto\b", r"\bcuantos\b", r"\bcuantas\b", r"\bultimos\b", r"\bhace\b",
        r"\bsemana\b", r"\bmes\b", r"\bpasado\b", r"\banterior\b", r"\bproximo\b", r"\bviene\b",
        
        # Lógica condicional o secuencial
        r"\bsi\s", r"\bentonces\b", r"\bluego\b", r"\bdespues\b", r"\bantes\b"
    ]

    def __init__(self):
        self.intents = get_intents()

    def route(self, text: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_text(text)
        words = normalized.split()
        
        # 1. Tier 1: Límite estricto de 5 palabras
        if len(words) > 5:
            return None
            
        # 2. Tier 2: Filtro de complejidad (anti-intercept)
        for pattern in self.COMPLEX_REGEX:
            if re.search(pattern, normalized):
                # Excepciones: palabras aceptables en contextos determinísticos (Fecha/Hora)
                if any(k in normalized for k in ["fecha", "dia", "hora"]):
                    if pattern in [r"\bhoy\b", r"\bcomo\b", r"\bcual\b"]:
                        continue
                return None
            
        # 3. Mapeo de Intenciones
        for intent in self.intents:
            result = intent.match_and_map(text, normalized)
            if result:
                if "__tool_override__" in result["tool_args"]:
                    result["tool_name"] = result["tool_args"].pop("__tool_override__")
                return result
                
        return None
