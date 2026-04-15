import os
import logging
import requests
import re
from duckduckgo_search import DDGS
from langchain_core.tools import tool

logger = logging.getLogger("denver.tools.websearch")

def _get_web_search_max_results() -> int:
    return int(os.getenv("WEB_SEARCH_MAX_RESULTS", 5))

def _get_web_search_region() -> str:
    return os.getenv("WEB_SEARCH_REGION", "es-ar")

def _get_web_fetch_max_chars() -> int:
    return int(os.getenv("WEB_FETCH_MAX_CHARS", 8000))

def _get_web_fetch_timeout() -> int:
    return int(os.getenv("WEB_FETCH_TIMEOUT", 15))

def _truncate(text: str, limit: int) -> str:
    """Trunca el texto si supera el límite."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... CONTENIDO TRUNCADO. Total original: {len(text)} caracteres ...]"

@tool
def web_search(query: str, max_results: int = None, region: str = None) -> str:
    """
    Realiza una búsqueda en internet usando DuckDuckGo.
    Útil para obtener información actualizada, noticias o datos que el modelo desconoce.
    """
    max_results = max_results or _get_web_search_max_results()
    region = region or _get_web_search_region()
    
    logger.info(f"Buscando en DuckDuckGo: '{query}' (max={max_results}, region={region})")
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region=region, max_results=max_results))
        
        if not results:
            return "No se encontraron resultados para la búsqueda."
        
        formatted_results = []
        for i, res in enumerate(results, 1):
            title = res.get('title', 'Sin título')
            snippet = res.get('body', 'Sin descripción')
            # Truncar snippet individual si es muy largo
            snippet = snippet[:1000] + "..." if len(snippet) > 1000 else snippet
            url = res.get('href', 'Sin URL')
            formatted_results.append(f"{i}. **{title}**\n   {snippet}\n   Fuente: {url}")
            
        final_text = "\n\n".join(formatted_results)
        # Límite global razonable para búsqueda (ej: 4000 chars)
        return _truncate(final_text, 4000)
    except Exception as e:
        logger.error(f"Error en web_search: {e}")
        return f"Error al realizar la búsqueda en internet: {e}"

@tool
def web_fetch(url: str, max_chars: int = None) -> str:
    """
    Lee el contenido de una URL específica usando Jina Reader (https://r.jina.ai/).
    Útil para profundizar en un resultado de búsqueda o leer una página web indicada por el usuario.
    """
    max_chars = max_chars or _get_web_fetch_max_chars()
    timeout = _get_web_fetch_timeout()
    
    logger.info(f"Extrayendo contenido de URL: {url} (limit={max_chars})")
    
    try:
        jina_url = f"https://r.jina.ai/{url}"
        response = requests.get(jina_url, timeout=timeout)
        response.raise_for_status()
        
        text = response.text
        original_size = len(text)
        
        # Limpieza según requerimientos
        lines = text.splitlines()
        cleaned_lines = []
        for line in lines:
            line_stripped = line.strip()
            # Eliminar líneas que sean solo URLs
            if re.match(r'^https?://[^\s]+$', line_stripped):
                continue
            # Eliminar separadores ---
            if set(line_stripped) <= {'-', ' '}:
                continue
            # Eliminar referencias de imágenes ![]()
            line = re.sub(r'!\[.*?\]\(.*?\)', '', line)
            
            if line.strip():
                cleaned_lines.append(line.rstrip())
        
        # Colapsar múltiples líneas en blanco en una sola
        cleaned_text = ""
        last_was_empty = False
        for line in cleaned_lines:
            if line.strip():
                cleaned_text += line + "\n"
                last_was_empty = False
            else:
                if not last_was_empty:
                    cleaned_text += "\n"
                    last_was_empty = True
        
        cleaned_text = cleaned_text.strip()
        
        # Truncar al límite de caracteres
        return _truncate(cleaned_text, max_chars)
        
    except Exception as e:
        logger.error(f"Error en web_fetch ({url}): {e}")
        return f"Error al leer la URL o URL inválida: {e}"

WEB_SEARCH_TOOLKIT = [web_search, web_fetch]
