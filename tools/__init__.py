"""
Denver Bot — Synchronous tools for Home Assistant, Squeezebox, Google APIs, and utilities.
All tools use @tool decorator from langchain and are synchronous (blocking).
Specialist nodes invoke them via asyncio.to_thread().
"""

import os
import json
import logging
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders

import requests
from typing import Optional
import sympy
import feedparser
from langchain_core.tools import tool
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from tools.lyrics import lyrics_search

logger = logging.getLogger("denver.tools")

# ─── Tool output truncation ───────────────────────────────────────────────────

_MAX_TOOL_CHARS = 8000  # truncar outputs pesados ANTES de que lleguen al scratchpad del agente

# Side channel para imágenes de cámara — evita que el base64 entre al scratchpad del agente
_captured_image: bytes | None = None

# Cache para servicios de Google API (singleton)
_google_services = {}
_google_creds = None


def _truncate(text: str, limit: int = _MAX_TOOL_CHARS) -> str:
    """Trunca texto largo para que no desborde el contexto del agente."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncado, {len(text)} chars totales)"


# ─── Home Assistant config ────────────────────────────────────────────────────

HA_URL = os.getenv("HOME_ASSISTANT_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN")
HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

WEATHER_STATES = {
    "sunny": "Soleado", "clear-night": "Despejado (noche)",
    "cloudy": "Nublado", "partlycloudy": "Parcialmente nublado",
    "rainy": "Lluvia", "pouring": "Lluvia intensa",
    "snowy": "Nieve", "snowy-rainy": "Aguanieve",
    "windy": "Ventoso", "windy-variant": "Ventoso con nubes",
    "fog": "Niebla", "hail": "Granizo",
    "lightning": "Tormenta eléctrica", "lightning-rainy": "Tormenta con lluvia",
    "exceptional": "Condiciones excepcionales",
}
 
# ─── Home Assistant cache ─────────────────────────────────────────────────────
_HA_ENTITIES_CACHE: list[dict] | None = None
_HA_RESOLVE_CACHE: dict[tuple[str, str | None], tuple[str | None, str, datetime]] = {}
_HA_RESOLVE_TTL = timedelta(hours=1)


def invalidate_ha_cache():
    """Limpia el cache de entidades de HA para forzar un refresco total."""
    global _HA_ENTITIES_CACHE, _HA_RESOLVE_CACHE
    _HA_ENTITIES_CACHE = None
    _HA_RESOLVE_CACHE = {}
    logger.info("Cache de Home Assistant invalidado.")


def get_ha_entities() -> list[dict]:
    """Obtiene la lista completa de entidades de HA, usando el cache si está disponible."""
    global _HA_ENTITIES_CACHE
    if _HA_ENTITIES_CACHE is not None:
        logger.info(f"Cache HIT: Usando lista de {len(_HA_ENTITIES_CACHE)} entidades cacheada.")
        return _HA_ENTITIES_CACHE

    try:
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code == 200:
            # No guardamos el estado en el cache para ahorrar memoria y evitar datos obsoletos.
            # Solo guardamos lo necesario para la resolución fuzzy (id y nombre).
            _HA_ENTITIES_CACHE = []
            for e in resp.json():
                eid = e.get("entity_id", "")
                attrs = e.get("attributes", {}) or {} # Asegurar que attributes es un dict
                fname = attrs.get("friendly_name")
                # Fallback: si friendly_name es None o vacío, usar eid
                _HA_ENTITIES_CACHE.append({
                    "entity_id": eid,
                    "attributes": {"friendly_name": fname if fname else eid}
                })
            return _HA_ENTITIES_CACHE
        else:
            logger.error(f"Error fetching HA entities: HTTP {resp.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error fetching HA entities: {e}")
        return []


def resolve_entity(hint: str, domain: str = None) -> tuple[str | None, str]:
    """
    Resuelve un entity_id a partir de un nombre parcial o fuzzy.
    Retorna (entity_id, friendly_name) o (None, error_msg).

    Prioridad:
    1. entity_id exacto existe → retornarlo
    2. Buscar por domain + hint en friendly_name / entity_id
    3. Fuzzy: palabras del hint coinciden con entity_id o nombre
    """
    try:
        if not hint:
            return None, "Hint de búsqueda vacío"
        hint_lower = hint.lower().strip()
        
        # ─── 1. Check Cache ───────────────────────────────────────────────────
        cache_key = (hint_lower, domain)
        if cache_key in _HA_RESOLVE_CACHE:
            eid, name, ts = _HA_RESOLVE_CACHE[cache_key]
            if datetime.now() - ts < _HA_RESOLVE_TTL:
                logger.info(f"Cache HIT: Mapeo resolve '{hint}' -> {eid}")
                return eid, name
        
        # 0. Check for bulk keywords (all/todas)
        bulk_keywords = ["todo", "toda", "todos", "todas", "all"]
        is_bulk = any(hint_lower == k or hint_lower.startswith(k + " ") for k in bulk_keywords)
        
        if is_bulk:
            # Try to infer domain if not specified or generic
            search_domain = domain
            if not search_domain or search_domain == "homeassistant":
                if any(k in hint_lower for k in ["luz", "luces"]):
                    search_domain = "light"
                elif any(k in hint_lower for k in ["enchufe", "enchufes", "switch"]):
                    search_domain = "switch"
                elif any(k in hint_lower for k in ["media", "musica", "reproductor"]):
                    search_domain = "media_player"
            
            # Get all matching entities
            entities = get_ha_entities()
            if entities:
                bulk_eids = []
                for e in entities:
                    eid = e["entity_id"]
                    if search_domain and not eid.startswith(f"{search_domain}."):
                        continue
                    # Skip problematic entities for bulk actions
                    if any(kw in eid.lower() for kw in ["group", "all", "browser"]):
                        continue
                    bulk_eids.append(eid)
                
                if bulk_eids:
                    return ",".join(bulk_eids), f"todas las entidades de {search_domain or 'todos los dominios'}"

        # 1. Exact match — only if hint looks like an entity_id (contains a dot)
        if "." in hint:
            try:
                resp = requests.get(f"{HA_URL}/api/states/{hint}", headers=HA_HEADERS, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    res = (hint, data.get("attributes", {}).get("friendly_name", hint))
                    _HA_RESOLVE_CACHE[cache_key] = (*res, datetime.now())
                    return res
            except Exception:
                pass  # fall through to fuzzy matching

        # 2. Get all states for fuzzy matching
        entities = get_ha_entities()
        if not entities:
            return None, "Error consultando entidades de Home Assistant (cache vacío)"

        hint_lower = hint.lower()
        
        # Stop word filtering for better fuzzy matching
        stop_words = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "mi", "mis", "tu", "tus", "su", "sus", "estado", "de la", "de las", "de los"}
        hint_words = [w for w in hint_lower.replace("_", " ").replace(".", " ").split() if w not in stop_words]
        if not hint_words: # fallback if all words were stop words
            hint_words = hint_lower.replace("_", " ").replace(".", " ").split()
        hint_words = set(hint_words)

        # Auto-detect domain from hint if not specified
        if not domain and "." in hint:
            domain = hint.split(".")[0]
            hint_remainder = hint.split(".", 1)[1].lower()
            hint_words_domain = [w for w in hint_remainder.replace("_", " ").split() if w not in stop_words]
            if hint_words_domain:
                hint_words = set(hint_words_domain)

        candidates = []
        for entity in entities:
            eid = entity.get("entity_id", "")
            name = entity.get("attributes", {}).get("friendly_name")
            if not name:
                name = eid
            name_lower = name.lower()
            eid_lower = eid.lower()

            # Apply domain filter
            if domain and domain != "homeassistant" and not eid.startswith(f"{domain}."):
                continue

            # Score matching
            score = 0

            # Exact substring in entity_id
            if hint_lower in eid_lower:
                score += 10

            # Exact substring in friendly_name
            if hint_lower in name_lower:
                score += 8

            # Word-level matching
            name_words = set(name_lower.replace("_", " ").split())
            eid_words = set(eid_lower.replace(".", " ").replace("_", " ").split())
            all_words = name_words | eid_words

            matching_words = hint_words & all_words
            if matching_words:
                score += len(matching_words) * 3

            # ONLY apply tie-breakers if the entity actually matches the query textually first
            if score > 0:
                # Tie-breaker: strongly prefer switch over light if both have the same name and generic domain is used
                if domain == "homeassistant":
                    if eid_lower.startswith("switch."):
                        score += 20
                    elif eid_lower.startswith("automation.") or eid_lower.startswith("sensor."):
                        # Penalize automations and sensors when the user wants to turn something on/off
                        score -= 20

                if score > 0: # Check again in case penalty dropped it below 0
                    candidates.append((score, eid, name))

        if not candidates:
            # Si falló la resolución, invalidamos el cache de lista completa por las dudas
            # de que el dispositivo sea nuevo y no esté en la lista vieja.
            invalidate_ha_cache()
            domain_info = f" en domain '{domain}'" if domain else ""
            return None, f"No se encontró entidad para '{hint}'{domain_info}."

        # Sort by score descending, then by entity_id length (shorter = more specific)
        candidates.sort(key=lambda x: (-x[0], len(x[1])))
        best_eid = candidates[0][1]
        best_name = candidates[0][2]
        
        # Save to cache
        _HA_RESOLVE_CACHE[cache_key] = (best_eid, best_name, datetime.now())
        
        return best_eid, best_name
    except Exception as e:
        invalidate_ha_cache()  # Refresh on error
        return None, f"Error resolviendo entidad: {e}"


def resolve_media_player(hint: str) -> tuple[str | None, str]:
    """
    Versión especializada de resolution para media_players.
    Prioriza reproductores que estén activamente reproduciendo o tengan datos de media.
    Orden de prioridad:
    1. media_player en estado 'playing'
    2. media_player con media_title (tiene datos de reproducción, aunque esté pausado/off)
    3. media_player en estado no-unavailable (paused, idle, on, off, standby)
    4. Cualquier media_player que coincida por nombre
    """
    generic_hints = {"musica", "reproductor", "sonido", "spotify", "lms",
                     "squeezebox", "music", "squeeze", "player", "parlante"}
    
    try:
        # Los media players SIEMPRE live porque necesitamos su estado actual
        # (playing, has_media) para priorizar la resolución.
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, f"Error HTTP {resp.status_code} consultando HA"
        
        entities = resp.json()
        hint_lower = hint.lower().replace("media_player.", "")
        is_generic = hint_lower in generic_hints
        
        playing = []       # state == playing
        has_media = []      # has media_title (recently played)
        not_unavailable = [] # any usable state
        any_match = []      # matches hint regardless of state
        
        for e in entities:
            eid = e.get("entity_id", "")
            if not eid.startswith("media_player."):
                continue
            
            state = e.get("state", "unknown")
            attrs = e.get("attributes", {}) or {}
            name = attrs.get("friendly_name") or eid
            name_lower = name.lower()
            eid_lower = eid.lower()
            has_title = bool(attrs.get("media_title"))
            
            # Check if hint matches this entity
            matches_hint = (is_generic or
                            hint_lower in eid_lower or
                            hint_lower in name_lower)
            
            entry = (eid, name, matches_hint)
            
            if state == "playing":
                playing.append(entry)
            
            if has_title:
                has_media.append(entry)
            
            if state != "unavailable":
                not_unavailable.append(entry)
            
            if matches_hint:
                any_match.append(entry)
        
        # Pick best candidate by priority tier
        for candidates in [playing, has_media, not_unavailable, any_match]:
            if not candidates:
                continue
            # Prefer ones that match the hint
            hint_matches = [c for c in candidates if c[2]]
            if hint_matches:
                return hint_matches[0][0], hint_matches[0][1]
            # If generic hint, return first available
            if is_generic:
                return candidates[0][0], candidates[0][1]
        
        return None, f"No se encontró un reproductor activo para '{hint}'."
    except Exception as e:
        return None, f"Error resolviendo media player: {e}"


# ─── Google APIs helpers ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
]


def get_google_service(service_name: str, version: str):
    """
    Inicializa un servicio Google API cargando token.json (singleton).
    Refresca el token automáticamente si está expirado e invalida el cache.
    Retorna None si no hay credenciales válidas.
    """
    global _google_creds, _google_services
    try:
        refreshed = False
        if _google_creds is None:
            if os.path.exists("token.json"):
                _google_creds = Credentials.from_authorized_user_file("token.json", SCOPES)

        if not _google_creds or not _google_creds.valid:
            if _google_creds and _google_creds.expired and _google_creds.refresh_token:
                _google_creds.refresh(Request())
                with open("token.json", "w") as token_file:
                    token_file.write(_google_creds.to_json())
                refreshed = True
            else:
                logger.warning("No hay credenciales válidas de Google. Ejecutar setup_google_auth.py.")
                return None

        # Si el token fue refrescado, invalidamos el cache de servicios antiguos
        if refreshed:
            _google_services = {}

        cache_key = f"{service_name}_{version}"
        if cache_key not in _google_services:
            _google_services[cache_key] = build(service_name, version, credentials=_google_creds)
        
        return _google_services[cache_key]
    except Exception as e:
        logger.error(f"Error inicializando servicio Google {service_name}: {e}")
        return None


# ─── Squeezebox helpers ───────────────────────────────────────────────────────

def _squeezebox_query(entity_id: str, command: str, parameters: list) -> str:
    """Ejecuta una consulta de lectura en Squeezebox/LMS vía HA."""
    try:
        payload = {
            "entity_id": entity_id,
            "command": command,
            "parameters": parameters,
        }
        resp = requests.post(
            f"{HA_URL}/api/services/squeezebox/call_query",
            headers=HA_HEADERS,
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return f"Consulta ejecutada. Respuesta: {resp.text[:500]}"
        return f"Error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"Error ejecutando consulta Squeezebox: {e}"


def _squeezebox_method(entity_id: str, command: str, parameters: list) -> str:
    """Ejecuta un método de acción en Squeezebox/LMS vía HA, con fallback a servicios estándar."""
    try:
        payload = {
            "entity_id": entity_id,
            "command": command,
            "parameters": parameters,
        }
        resp = requests.post(
            f"{HA_URL}/api/services/squeezebox/call_method",
            headers=HA_HEADERS,
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return f"Método ejecutado exitosamente."
        
        # Fallback: if squeezebox service fails (400), try standard HA media_player services
        ha_service_map = {
            "pause": "media_player/media_pause",
            "play": "media_player/media_play",
            "stop": "media_player/media_stop",
            "power": "media_player/toggle",
        }
        
        fallback_service = ha_service_map.get(command)
        fallback_payload = {"entity_id": entity_id}
        
        # Handle volume: mixer volume XX → media_player/volume_set
        if command == "mixer" and parameters and parameters[0] == "volume":
            fallback_service = "media_player/volume_set"
            try:
                vol = float(parameters[1]) / 100.0  # LMS uses 0-100, HA uses 0-1
                fallback_payload["volume_level"] = vol
            except (IndexError, ValueError):
                pass
        
        if fallback_service:
            fb_resp = requests.post(
                f"{HA_URL}/api/services/{fallback_service}",
                headers=HA_HEADERS,
                json=fallback_payload,
                timeout=10,
            )
            if fb_resp.status_code == 200:
                return f"Método ejecutado exitosamente."
            return f"Error {fb_resp.status_code}: {fb_resp.text[:200]}"
        
        return f"Error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"Error ejecutando método Squeezebox: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# HOME ASSISTANT — CONSULTAS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def get_all_lights_status() -> str:
    """
    Get the status of ALL lights in Home Assistant.
    Returns lists of on and off lights with friendly_name and entity_id.
    Use when the user asks for the general status of the lights.
    Do not use for a specific light — use get_ha_status(entity_id).
    """
    try:
        # Estado de luces SIEMPRE live
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error obteniendo estados: HTTP {resp.status_code}"

        states = resp.json()
        encendidas = []
        apagadas = []

        for entity in states:
            eid = entity.get("entity_id", "")
            if not eid.startswith("light."):
                continue
            name = entity.get("attributes", {}).get("friendly_name", eid)
            state = entity.get("state", "unknown")
            brightness = entity.get("attributes", {}).get("brightness")

            if state == "on":
                brillo_info = f" ({round(brightness / 255 * 100)}%)" if brightness else ""
                encendidas.append(f"  💡 {name}{brillo_info}")
            else:
                apagadas.append(f"  ⚫ {name}")

        result = f"🔆 Encendidas ({len(encendidas)}):\n"
        result += "\n".join(encendidas) if encendidas else "  Ninguna"
        result += f"\n\n🌑 Apagadas ({len(apagadas)}):\n"
        result += "\n".join(apagadas) if apagadas else "  Ninguna"
        return _truncate(result)
    except Exception as e:
        return f"Error consultando luces: {e}"


@tool
def get_ha_status(entity_id: str) -> str:
    """
    Get the detailed status of a specific Home Assistant entity.
    Accepts exact entity_id OR partial/fuzzy name.
    Works with lights, sensors, switches, media_players, and any entity.
    For media_players: shows what's playing, artist, album, volume.
    """
    try:
        # Detect media player hints and use specialized resolver
        _media_hints = {"musica", "reproductor", "sonido", "spotify", "lms",
                        "squeezebox", "music", "squeeze", "media_player",
                        "player", "parlante", "bocina", "speaker"}
        hint_lower = entity_id.lower().replace("_", " ").replace(".", " ")
        is_media = (entity_id.startswith("media_player.") or
                    any(h in hint_lower for h in _media_hints))

        if is_media:
            resolved_eid, resolved_name = resolve_media_player(entity_id)
        else:
            # Smart entity resolution: try exact match first, then fuzzy
            resolved_eid, resolved_name = resolve_entity(entity_id, domain="homeassistant")

        if resolved_eid is None:
            return f"Entidad '{entity_id}' no encontrada. {resolved_name}"
        entity_id = resolved_eid

        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}: {resp.text[:200]}"

        data = resp.json()
        attrs = data.get("attributes", {})
        name = attrs.get("friendly_name", entity_id)
        state = data.get("state", "unknown")

        info = [f"📊 {name}: {state}"]

        # Light attributes
        if "brightness" in attrs:
            info.append(f"  Brillo: {round(attrs['brightness'] / 255 * 100)}%")
        if "rgb_color" in attrs:
            info.append(f"  Color RGB: {attrs['rgb_color']}")
        if "color_temp_kelvin" in attrs:
            info.append(f"  Temperatura de color: {attrs['color_temp_kelvin']}K")

        # Sensor attributes
        if "temperature" in attrs:
            info.append(f"  Temperatura: {attrs['temperature']}°C")
        if "humidity" in attrs:
            info.append(f"  Humedad: {attrs['humidity']}%")
        if "unit_of_measurement" in attrs and "temperature" not in attrs:
            info.append(f"  Unidad: {attrs['unit_of_measurement']}")

        # Media player attributes
        if "media_title" in attrs:
            info.append(f"  🎵 Canción: {attrs['media_title']}")
        if "media_artist" in attrs:
            info.append(f"  🎤 Artista: {attrs['media_artist']}")
        if "media_album_name" in attrs:
            info.append(f"  💿 Álbum: {attrs['media_album_name']}")
        if "volume_level" in attrs:
            vol = round(attrs['volume_level'] * 100)
            info.append(f"  🔊 Volumen: {vol}%")
        if "source" in attrs:
            info.append(f"  📡 Fuente: {attrs['source']}")
        if "media_duration" in attrs:
            dur = int(attrs['media_duration'])
            mins, secs = divmod(dur, 60)
            info.append(f"  ⏱️ Duración: {mins}:{secs:02d}")
        if "media_position" in attrs:
            pos = int(attrs['media_position'])
            mins, secs = divmod(pos, 60)
            info.append(f"  ⏳ Posición: {mins}:{secs:02d}")

        return "\n".join(info)
    except Exception as e:
        return f"Error consultando entidad: {e}"


@tool
def list_ha_entities(domain: str = None, search_term: str = None) -> str:
    """
    List available entities in Home Assistant. Limits to 50 results.
    domain: 'light' | 'switch' | 'sensor' | 'climate' | 'media_player' | 'camera' | etc.
    search_term: text that appears in entity_id or friendly_name.
    Use before execute_ha_command when the exact entity_id is not known.
    """
    try:
        # Lista de entidades con estado SIEMPRE live
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}"

        results = []
        for entity in resp.json():
            eid = entity.get("entity_id", "")
            name = entity.get("attributes", {}).get("friendly_name", eid)
            state = entity.get("state", "unknown")

            if domain and not eid.startswith(f"{domain}."):
                continue
            if search_term:
                search_lower = search_term.lower()
                if search_lower not in eid.lower() and search_lower not in name.lower():
                    continue

            results.append(f"  • {name} | {eid} | {state}")
            if len(results) >= 20:
                break

        if not results:
            return "No se encontraron entidades con esos filtros."
        return _truncate(f"Entidades encontradas ({len(results)}):\n" + "\n".join(results))
    except Exception as e:
        return f"Error listando entidades: {e}"


@tool
def get_all_sensors_status() -> str:
    """
    Get the status of ALL sensors in Home Assistant.
    Groups by type: temperature, humidity, others.
    Use when the user asks for a complete environmental status overview.
    """
    try:
        # Estado de sensores SIEMPRE live
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}"

        temp_sensors = []
        humidity_sensors = []
        presence_sensors = []
        person_tracking = []
        other_sensors = []

        for entity in resp.json():
            eid = entity.get("entity_id", "")
            if not (eid.startswith("sensor.") or eid.startswith("binary_sensor.") or eid.startswith("person.")):
                continue
            attrs = entity.get("attributes", {})
            name = attrs.get("friendly_name", eid)
            state = entity.get("state", "unknown")
            unit = attrs.get("unit_of_measurement", "")
            device_class = attrs.get("device_class", "")

            # Normalizar estado para binary_sensors
            if eid.startswith("binary_sensor."):
                state_icon = "🟢" if state == "on" else "⚪"
                display_state = "Activado" if state == "on" else "Desactivado"
                # Traducir según device_class
                if device_class == "motion":
                    display_state = "Movimiento" if state == "on" else "Sin movimiento"
                elif device_class == "occupancy":
                    display_state = "Ocupado" if state == "on" else "Libre"
                elif device_class == "door" or device_class == "window":
                    display_state = "Abierta" if state == "on" else "Cerrada"
                entry = f"  {state_icon} {name}: {display_state}"
            else:
                entry = f"  • {name}: {state}{unit}"

            if device_class == "temperature" or "temperatura" in name.lower():
                temp_sensors.append(entry)
            elif device_class == "humidity" or "humedad" in name.lower():
                humidity_sensors.append(entry)
            elif device_class in ["motion", "occupancy", "presence"] or any(kw in name.lower() or kw in eid.lower() for kw in ["motion", "movimiento", "presencia", "presence", "ocupacion", "dsc", "zone"]):
                presence_sensors.append(entry)
            elif eid.startswith("person."):
                person_tracking.append(f"  👤 {name}: {state}")
            else:
                # Evitar saturar con sensores técnicos (batería, señal, etc) si no se pidió explícitamente
                technical_keywords = ["battery", "bateria", "linkquality", "signal", "voltage", "update", "restart"]
                if not any(kw in eid.lower() or kw in name.lower() for kw in technical_keywords):
                    other_sensors.append(entry)

        result = f"🌡️ Temperatura ({len(temp_sensors)}):\n"
        result += "\n".join(temp_sensors) if temp_sensors else "  Ninguno"
        result += f"\n\n💧 Humedad ({len(humidity_sensors)}):\n"
        result += "\n".join(humidity_sensors) if humidity_sensors else "  Ninguno"
        result += f"\n\n🚶 Presencia/Movimiento ({len(presence_sensors)}):\n"
        result += "\n".join(presence_sensors) if presence_sensors else "  Ninguno"
        result += f"\n\n👥 Personas (Tracking) ({len(person_tracking)}):\n"
        result += "\n".join(person_tracking) if person_tracking else "  Ninguna"
        result += f"\n\n📊 Otros ({len(other_sensors)}):\n"
        result += "\n".join(other_sensors[:15]) if other_sensors else "  Ninguno"
        if len(other_sensors) > 15:
            result += f"\n  ... y {len(other_sensors) - 15} más"
        return _truncate(result)
    except Exception as e:
        return f"Error consultando sensores: {e}"


@tool
def get_sensors_by_type(sensor_type: str) -> str:
    """
    Get sensors filtered by type.
    sensor_type: HA device_class ('temperature' | 'humidity' | 'pressure' |
    'light' | 'motion' | 'door') or any text in name or entity_id.
    More efficient than get_all_sensors_status for specific queries.
    """
    try:
        # SIEMPRE live
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}"

        results = []
        search = sensor_type.lower()

        for entity in resp.json():
            eid = entity.get("entity_id", "")
            if not (eid.startswith("sensor.") or eid.startswith("binary_sensor.") or eid.startswith("person.")):
                continue
            attrs = entity.get("attributes", {})
            name = attrs.get("friendly_name", eid)
            state = entity.get("state", "unknown")
            unit = attrs.get("unit_of_measurement", "")
            device_class = attrs.get("device_class", "") or ""

            # Check for type match (either in device_class, entity_id or friendly_name)
            # Handle "presence" or "person" as a special alias
            type_keywords = [search]
            if search in ["presence", "presencia"]:
                type_keywords.extend(["motion", "movimiento", "occupancy", "ocupacion", "dsc", "zone"])
            elif search in ["person", "persona", "personas", "tracking"]:
                type_keywords.extend(["person.", "tracker"])

            match = False
            for kw in type_keywords:
                if kw in device_class.lower() or kw in eid.lower() or kw in name.lower():
                    match = True
                    break
            
            if match:
                if eid.startswith("binary_sensor."):
                    state_icon = "🟢" if state == "on" else "⚪"
                    results.append(f"  {state_icon} {name}: {state}")
                elif eid.startswith("person."):
                    results.append(f"  👤 {name}: {state}")
                else:
                    results.append(f"  • {name}: {state}{unit}")

        if not results:
            return f"No se encontraron sensores de tipo '{sensor_type}'."
        return f"Sensores de '{sensor_type}' ({len(results)}):\n" + "\n".join(results)
    except Exception as e:
        return f"Error consultando sensores: {e}"


@tool
def get_weather_forecast(entity_id: Optional[str] = None) -> str:
    """
    Get current weather and forecast from Home Assistant.
    If entity_id is not provided, it auto-discovers the first available weather entity.
    Use for any question about weather, outdoor temperature, rain, or forecast.
    """
    try:
        # If no entity_id, try to find one
        if not entity_id:
            entities = get_ha_entities()
            weather_entities = [e for e in entities if e.get("entity_id", "").startswith("weather.")]
            if not weather_entities:
                return "No se encontraron entidades de clima en Home Assistant."
            entity_id = weather_entities[0]["entity_id"]

        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=10)

        # Fallback if specific entity not found
        if resp.status_code == 404:
            entities = get_ha_entities()
            weather_entities = [e for e in entities if e.get("entity_id", "").startswith("weather.")]
            if weather_entities:
                entity_id = weather_entities[0]["entity_id"]
                resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=10)
            else:
                return f"Entidad '{entity_id}' no encontrada y no se hallaron alternativas."

        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}"

        data = resp.json()
        attrs = data.get("attributes", {})
        state = data.get("state", "unknown")
        condicion = WEATHER_STATES.get(state, state)
        temp = attrs.get("temperature", "N/A")
        temp_unit = attrs.get("temperature_unit", "°C")
        humidity = attrs.get("humidity", "N/A")
        wind = attrs.get("wind_speed", "N/A")

        result = f"🌤️ Clima: {condicion} | 🌡️ {temp}{temp_unit} | 💧 {humidity}% | 💨 {wind} km/h"

        forecast = attrs.get("forecast", [])
        if forecast:
            result += "\n\n📅 Pronóstico:"
            for day in forecast[:5]:
                fecha = day.get("datetime", "")[:10]
                cond = WEATHER_STATES.get(day.get("condition", ""), day.get("condition", ""))
                temp_min = day.get("templow", "?")
                temp_max = day.get("temperature", "?")
                result += f"\n  • {fecha}: {cond} {temp_min}°/{temp_max}°C"

        return result
    except Exception as e:
        return f"Error consultando clima: {e}"


@tool
def capture_camera_image(entity_id: str) -> str:
    """
    Capture an image from a Home Assistant camera.
    Accepts exact entity_id OR partial/fuzzy name (e.g., 'gallery', 'pool', 'entrance').
    The tool automatically resolves the correct entity_id.
    The image is saved internally and sent to the user automatically.
    """
    global _captured_image
    try:
        # Smart entity resolution: fuzzy match camera name
        resolved_eid, resolved_name = resolve_entity(entity_id, domain="camera")
        if resolved_eid is None:
            return f"Cámara '{entity_id}' no encontrada. {resolved_name}"
        entity_id = resolved_eid

        # Obtener el estado para verificar que existe y obtener entity_picture
        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=10)
        if resp.status_code != 200:
            return f"Error HTTP {resp.status_code}"

        data = resp.json()
        entity_picture = data.get("attributes", {}).get("entity_picture")
        if not entity_picture:
            return f"La entidad '{entity_id}' no tiene imagen disponible."

        # Descargar la imagen y guardarla en el side channel (NO devolverla como texto)
        img_url = f"{HA_URL}{entity_picture}"
        img_resp = requests.get(img_url, headers=HA_HEADERS, timeout=15)
        if img_resp.status_code != 200:
            return f"Error descargando imagen: HTTP {img_resp.status_code}"

        _captured_image = img_resp.content
        name = data.get("attributes", {}).get("friendly_name", entity_id)
        return f"✅ Imagen capturada de '{name}'. Se enviará al usuario automáticamente."
    except Exception as e:
        return f"Error capturando imagen de cámara: {e}"


@tool
def get_ha_history(entity_id: str, start_time: str = None, end_time: str = None) -> str:
    """
    Get the state history of a Home Assistant entity over a specific period.
    Accepts exact entity_id OR partial/fuzzy name. Useful to know when something was turned on/off yesterday or today.
    IMPORTANT: Never include words like 'yesterday' or 'today' in the entity_id. Just the device name.
    - start_time: Start date/time in ISO 8601 with offset (e.g., '2026-03-09T00:00:00-03:00')
    - end_time: (Optional) End date/time in ISO 8601 with offset (e.g., '2026-03-09T23:59:59-03:00')
    If not passed, it searches the last 24 hours.
    """
    try:
        # Extraer "ayer" o "hoy" erróneamente pasados en entity_id para limpiar la query
        clean_eid = entity_id.lower().replace("de ayer", "").replace("de hoy", "").strip()
        
        resolved_eid, resolved_name = resolve_entity(clean_eid, domain="homeassistant")
        if resolved_eid is None:
            return f"Entidad '{clean_eid}' no encontrada. {resolved_name}"
        entity_id = resolved_eid

        if not start_time:
            # Por defecto buscamos las últimas 24hs
            start_time = (datetime.now() - timedelta(days=1)).astimezone().isoformat()
            
        url = f"{HA_URL}/api/history/period/{start_time}?filter_entity_id={entity_id}&minimal_response"
        if end_time:
            url += f"&end_time={end_time}"
            
        try:
            resp = requests.get(url, headers=HA_HEADERS, timeout=20)
        except requests.exceptions.Timeout:
            return f"La consulta de historial para '{resolved_name}' tardó demasiado. Intentá con un rango de tiempo menor."
        except Exception as conn_err:
            return f"Error de conexión consultando HA: {conn_err}"
        
        if resp.status_code != 200:
            logger.error(f"[HA History] Error {resp.status_code} for {entity_id}: {resp.text[:1000]}")
            # Si es 500, puede ser un error interno de HA por exceso de datos
            if resp.status_code == 500:
                return f"Error interno en Home Assistant (500). Es probable que haya demasiados datos para procesar en este rango."
            return f"Error HTTP {resp.status_code} de Home Assistant."
            
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.error(f"[HA History] JSONDecodeError for {entity_id}. Raw: {resp.text[:500]}")
            return "Error procesando la respuesta (JSON inválido) de Home Assistant."

        if not data or not isinstance(data, list) or len(data) == 0 or not data[0]:
            return f"No hay historial disponible para '{resolved_name}' ({entity_id}) en el periodo solicitado."
            
        history = data[0]
        
        lines = [f"📜 Historial de '{resolved_name}' ({entity_id}):"]
        
        last_state = None
        for entry in history:
            state = entry.get("state")
            # Ignorar estados desconocidos o repetidos consecutivamente
            if not state or state in ["unknown", "unavailable"] or state == last_state:
                continue
                
            last_changed = entry.get("last_changed")
            if last_changed:
                try:
                    # HA puede devolver Z o offset. Normalizamos a UTC si es Z.
                    dt = datetime.fromisoformat(last_changed.replace('Z', '+00:00'))
                    dt_local = dt.astimezone()
                    time_str = dt_local.strftime("%d/%m %H:%M:%S")
                    lines.append(f"  • {time_str}: {state}")
                    last_state = state
                except Exception:
                    lines.append(f"  • {last_changed}: {state}")
                    last_state = state
                    
        if len(lines) > 50:
            lines = lines[:1] + ["  ... (muchos cambios omitidos) ..."] + lines[-48:]
            
        if len(lines) == 1:
            return f"El estado de '{resolved_name}' ({entity_id}) no cambió en el periodo solicitado. Sigue '{last_state}'."
            
        return _truncate("\n".join(lines))
    except Exception as e:
        return f"Error consultando historial: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# HOME ASSISTANT — EJECUCIÓN
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def execute_ha_command(domain: str, service: str, entity_id: str,
                       service_data: dict = None) -> str:
    """
    Execute a Home Assistant service on an entity.
    Accepts exact entity_id OR partial/fuzzy name.
    The tool automatically resolves the correct entity_id.

    Examples (MANDATORY to pass domain, service, and entity_id):
    - Turn on light:    domain='homeassistant', service='turn_on',  entity_id='living light'
    - Turn off light:   domain='homeassistant', service='turn_off', entity_id='mezzanine light'
    - Arm alarm:        domain='alarm_control_panel', service='alarm_arm_away', entity_id='home_alarm', service_data={'code': '1111'}
    """
    try:
        # Smart entity resolution: fuzzy match entity name
        resolved_eid, resolved_name = resolve_entity(entity_id, domain=domain)
        if resolved_eid is None:
            return f"Entidad '{entity_id}' no encontrada. {resolved_name}"
        entity_id = resolved_eid

        # If multiple entities (bulk), we skip initial verification to avoid 404
        if "," in entity_id:
            payload = {"entity_id": entity_id.split(",")}
            if service_data:
                payload.update(service_data)
            resp = requests.post(
                f"{HA_URL}/api/services/{domain}/{service}",
                headers=HA_HEADERS,
                json=payload,
                timeout=15,
            )
            if resp.status_code == 200:
                return f"✅ Ejecutado {domain}.{service} sobre {len(entity_id.split(','))} entidades."
            return f"Error {resp.status_code}: {resp.text[:200]}"

        # Get initial state (single entity)
        initial_resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=5)
        initial_state = initial_resp.json().get("state") if initial_resp.status_code == 200 else None

        payload = {"entity_id": entity_id}
        if service_data:
            payload.update(service_data)

        resp = requests.post(
            f"{HA_URL}/api/services/{domain}/{service}",
            headers=HA_HEADERS,
            json=payload,
            timeout=10,
        )

        if resp.status_code == 200:
            import time
            # Single brief wait + one state check (replaces 5-iteration polling loop)
            time.sleep(0.3)
            final_state = initial_state
            try:
                f_resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=5)
                if f_resp.status_code == 200:
                    final_state = f_resp.json().get("state")
            except Exception:
                pass

            # Construir mensaje de éxito humanizado
            if service == "turn_on":
                verbo = "encendió" if domain == "light" else "activó"
                msg = f"✅ Se {verbo} {resolved_name}."
            elif service == "turn_off":
                verbo = "apagó" if domain == "light" else "desactivó"
                msg = f"✅ Se {verbo} {resolved_name}."
            elif service == "toggle":
                msg = f"✅ Se cambió el estado de {resolved_name}."
            elif service == "alarm_arm_away":
                msg = f"✅ Alarma armada (fuera) en {resolved_name}."
            elif service == "alarm_arm_home":
                msg = f"✅ Alarma armada (en casa) en {resolved_name}."
            elif service == "alarm_disarm":
                msg = f"✅ Alarma desarmada en {resolved_name}."
            else:
                msg = f"✅ Ejecutado: {domain}.{service} sobre {resolved_name}."
            
            # Warn if state didn't change
            if service in ["turn_on", "turn_off", "toggle", "alarm_arm_away", "alarm_arm_home", "alarm_disarm"] and initial_state and final_state:
                if initial_state == final_state:
                    msg += f"\n⚠️ La entidad sigue en '{final_state}'. Podría estar desconectado o requerir un código correcto."
                else:
                    # Si el estado es el esperado (on/off), no hace falta ser tan técnico
                    if (service == "turn_on" and final_state == "on") or (service == "turn_off" and final_state == "off"):
                        pass 
                    else:
                        msg += f" Estado confirmado: {final_state}."
            
            return msg
        return f"Error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"Error ejecutando comando: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# SQUEEZEBOX / LOGITECH MEDIA SERVER
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def squeezebox_call_query(entity_id: str, command: str, parameters: list = []) -> str:
    """
    Execute a read query on Squeezebox/LMS.
    Accepts exact entity_id OR partial/fuzzy name.
    Use for: player status, current playlist info, etc.
    Examples:
    - Status: command='status', parameters=[]
    - Playlist tracks: command='playlist', parameters=['tracks', '?']
    """
    resolved_eid, resolved_name = resolve_media_player(entity_id)
    if resolved_eid is None:
        return f"Reproductor '{entity_id}' no encontrado. {resolved_name}"
    return _squeezebox_query(resolved_eid, command, parameters or [])


@tool
def squeezebox_call_method(entity_id: str, command: str, parameters: list = []) -> str:
    """
    Execute an action method on Squeezebox/LMS.
    Accepts exact entity_id OR partial/fuzzy name.
    Use for: play, pause, stop, volume, skip track, etc.
    Examples:
    - Pause music: command='pause', parameters=[]
    - Play: command='play', parameters=[]
    - Stop: command='stop', parameters=[]
    - Volume: command='mixer', parameters=['volume', '50']
    - Next track: command='playlist', parameters=['index', '+1']
    """
    resolved_eid, resolved_name = resolve_media_player(entity_id)
    if resolved_eid is None:
        return f"Reproductor '{entity_id}' no encontrado. {resolved_name}"
    return _squeezebox_method(resolved_eid, command, parameters or [])


@tool
def squeezebox_loadtracks(entity_id: str, query: str) -> str:
    """
    Load and play music on Squeezebox using taggedParameters from LMS.
    Accepts exact entity_id OR partial/fuzzy name.
    Tries multiple strategies in order until a match is found:
    1. contributor.namesearch=Query
    2. artist.namesearch=Query
    3. album.titlesearch=Query
    4. track.titlesearch=Query
    5. Query without prefix (general fallback)

    Query examples:
    - "Miranda" → searches by artist
    - "track.titlesearch=Mariposa Tecknik" → exact track search
    """
    try:
        resolved_eid, resolved_name = resolve_media_player(entity_id)
        if resolved_eid is None:
            return f"Reproductor '{entity_id}' no encontrado. {resolved_name}"
        entity_id = resolved_eid

        # Si el query ya tiene un prefijo explícito, usarlo directamente
        if "." in query and "=" in query:
            strategies = [query]
        else:
            strategies = [
                f"contributor.namesearch={query}",
                f"artist.namesearch={query}",
                f"album.titlesearch={query}",
                f"track.titlesearch={query}",
                query,
            ]

        for strategy in strategies:
            payload = {
                "entity_id": entity_id,
                "command": "playlist",
                "parameters": ["loadtracks", strategy],
            }
            resp = requests.post(
                f"{HA_URL}/api/services/squeezebox/call_method",
                headers=HA_HEADERS,
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                # loadtracks accepted — return immediately.
                # The old verification via call_query was unreliable (400 errors)
                # and caused the tool to try ALL strategies, replacing the playlist
                # each time. If the API accepted it, trust it.
                import time
                time.sleep(1.5)  # Let LMS load & start playing
                strategy_name = strategy.split('=')[0] if '=' in strategy else 'general'
                return f"🎵 Reproduciendo: '{query}' en {resolved_name} (estrategia: {strategy_name})"

        return f"No se encontraron resultados para '{query}'."
    except Exception as e:
        return f"Error cargando música: {e}"


@tool
def squeezebox_playlist_track_count(entity_id: str) -> str:
    """
    Returns the number of tracks in the current player playlist.
    Accepts exact entity_id OR partial/fuzzy name.
    """
    resolved_eid, resolved_name = resolve_media_player(entity_id)
    if resolved_eid is None:
        return f"Reproductor '{entity_id}' no encontrado. {resolved_name}"
    return _squeezebox_query(resolved_eid, "playlist", ["tracks", "?"])


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE APIS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def list_calendar_events(calendar_id: str = "primary", max_results: int = 10,
                          days_ahead: int = 7) -> str:
    """
    List upcoming Google Calendar events.
    Returns: title, date, time, and location for each event.
    """
    try:
        service = get_google_service("calendar", "v3")
        if not service:
            return "❌ No hay credenciales de Google válidas. Ejecutar setup_google_auth.py."

        now = datetime.utcnow().isoformat() + "Z"
        time_max = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + "Z"

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return "📅 No hay eventos próximos."

        lines = [f"📅 Próximos eventos ({len(events)}):"]
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            title = event.get("summary", "Sin título")
            location = event.get("location", "")
            loc_info = f" 📍 {location}" if location else ""
            lines.append(f"  • {start} — {title}{loc_info}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error consultando calendario: {e}"


@tool
def create_calendar_event(summary: str, start_time: str, end_time: str,
                           description: str = "", location: str = "") -> str:
    """
    Create an event in Google Calendar. Returns the event's HTML link.
    ALWAYS ask the user for confirmation before executing.
    Time format: RFC3339 with mandatory timezone offset.
    - Argentina: '2025-03-15T10:00:00-03:00'
    """
    try:
        service = get_google_service("calendar", "v3")
        if not service:
            return "❌ No hay credenciales de Google válidas."

        event = {
            "summary": summary,
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        created = service.events().insert(calendarId="primary", body=event).execute()
        return f"✅ Evento creado: {created.get('htmlLink', 'ok')}"
    except Exception as e:
        return f"Error creando evento: {e}"


@tool
def list_gmail_messages(query: str = "", max_results: int = 5) -> str:
    """
    List Gmail messages using standard search syntax.
    Examples: 'is:unread' | 'from:boss@company.com' | 'subject:meeting'
    Returns: sender, subject, and snippet per message.
    """
    try:
        service = get_google_service("gmail", "v1")
        if not service:
            return "❌ No hay credenciales de Google válidas."

        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "📧 No se encontraron mensajes."

        lines = [f"📧 Mensajes ({len(messages)}):"]
        for msg_meta in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_meta["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "Desconocido")
            subject = headers.get("Subject", "Sin asunto")
            snippet = msg.get("snippet", "")[:100]

            lines.append(f"  • De: {sender}\n    Asunto: {subject}\n    {snippet}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error consultando Gmail: {e}"


@tool
def send_gmail_message(to: str, subject: str, body: str, attachments: Optional[list[str | dict]] = None) -> str:
    """
    Send an email from the configured Gmail account. Use this tool mandatorily to send emails.
    If there is a recently captured image (e.g., camera), it will be attached automatically.
    - attachments: Optional list of file paths (strings).
      IMPORTANT: Provide the full or relative PATH to the file, not just the name. 
      Example: ['denver_storage/luces.txt']
    """
    global _captured_image
    try:
        service = get_google_service("gmail", "v1")
        if not service:
            return "❌ No hay credenciales de Google válidas."

        if _captured_image or attachments:
            message = MIMEMultipart()
            message["to"] = to
            message["subject"] = subject
            message.attach(MIMEText(body))
            
            # Attach captured image if exists
            if _captured_image:
                img_part = MIMEImage(_captured_image)
                img_part.add_header('Content-Disposition', 'attachment', filename="captura.jpg")
                message.attach(img_part)
                _captured_image = None
                
            # Attach extra files if provided
            if attachments:
                for item in attachments:
                    # Robustness: handle case where agent passes a dict instead of string
                    file_path = item.get("filename") or item.get("path") or item.get("file") if isinstance(item, dict) else item
                    if not file_path:
                        continue

                    # Smart search: if file not found and is just a name, check denver_storage
                    if not os.path.exists(file_path):
                        if "/" not in file_path and "\\" not in file_path:
                            alt_path = os.path.join("denver_storage", file_path)
                            if os.path.exists(alt_path):
                                file_path = alt_path
                            else:
                                logger.warning(f"Attachment not found: {file_path}")
                                continue
                        else:
                            logger.warning(f"Attachment not found: {file_path}")
                            continue
                    
                    try:
                        with open(file_path, "rb") as f:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(f.read())
                        
                        encoders.encode_base64(part)
                        filename = os.path.basename(file_path)
                        part.add_header("Content-Disposition", f"attachment; filename={filename}")
                        message.attach(part)
                    except Exception as fe:
                        logger.error(f"Error attaching file {file_path}: {fe}")
        else:
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        attached_count = 0
        if isinstance(message, MIMEMultipart):
            attached_count = len(message.get_payload()) - 1 # Subtract body
            
        att_msg = f" (con {attached_count} adjuntos)" if attached_count > 0 else ""
        return f"✅ Correo enviado a {to}: '{subject}'{att_msg}"
    except Exception as e:
        return f"Error enviando correo: {e}"


@tool
def search_google_contacts(query: str) -> str:
    """
    Search in Google Contacts (People API v1) by name, email, or phone.
    Returns: name, email, and phone for each found contact.
    """
    try:
        service = get_google_service("people", "v1")
        if not service:
            return "❌ No hay credenciales de Google válidas."

        # Búsqueda real
        result = service.people().searchContacts(
            query=query, pageSize=10,
            readMask="names,emailAddresses,phoneNumbers"
        ).execute()

        contacts = result.get("results", [])
        if not contacts:
            return f"No se encontraron contactos para '{query}'."

        lines = [f"👤 Contactos ({len(contacts)}):"]
        for contact in contacts:
            person = contact.get("person", {})
            names = person.get("names", [{}])
            name = names[0].get("displayName", "Sin nombre") if names else "Sin nombre"
            emails = person.get("emailAddresses", [])
            phones = person.get("phoneNumbers", [])

            email_str = emails[0].get("value", "") if emails else "N/A"
            phone_str = phones[0].get("value", "") if phones else "N/A"

            lines.append(f"  • {name} | ✉️ {email_str} | 📞 {phone_str}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error buscando contactos: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

# Wikipedia
_wiki_wrapper = WikipediaAPIWrapper(lang="es", top_k_results=2, doc_content_chars_max=2000)
_wiki_tool = WikipediaQueryRun(api_wrapper=_wiki_wrapper)


@tool
def wikipedia_search(query: str) -> str:
    """
    Search in Wikipedia. Use for encyclopedic questions, definitions,
    people, places, history.
    """
    try:
        return _wiki_tool.run(query)
    except Exception as e:
        return f"Error buscando en Wikipedia: {e}"


@tool
def get_current_news(query: str = "general") -> str:
    """
    Get current news from Google News RSS (no API key required).
    Returns top 5 articles with title and date.
    """
    try:
        if query and query != "general":
            url = f"https://news.google.com/rss/search?q={query}&hl=es&gl=AR&ceid=AR:es"
        else:
            url = "https://news.google.com/rss?hl=es&gl=AR&ceid=AR:es"

        feed = feedparser.parse(url)
        entries = feed.entries[:5]

        if not entries:
            return "No se encontraron noticias."

        lines = ["📰 Noticias:"]
        for entry in entries:
            title = entry.get("title", "Sin título")
            published = entry.get("published", "")
            #link = entry.get("link", "")
            #lines.append(f"  • {title}\n    {published}\n    {link}")
            lines.append(f"  • {title}\n    {published}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo noticias: {e}"


@tool
def calculator(expression: str) -> str:
    """
    Safely evaluate mathematical expressions using sympy.
    Does not use eval() — it is safe against code injection.
    """
    try:
        result = sympy.sympify(expression, evaluate=True)
        return f"🔢 {expression} = {result}"
    except Exception as e:
        return f"Error evaluando expresión: {e}"


@tool
def get_current_datetime() -> str:
    """
    Returns the current system date and time with the day of the week.
    Use for any question about the current date, time, or day.
    """
    now = datetime.now()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia = dias[now.weekday()]
    return f"Fecha: {now.strftime('%d/%m/%Y')} | Hora: {now.strftime('%H:%M:%S')} | Día: {dia}"
