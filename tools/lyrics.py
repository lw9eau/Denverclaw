"""
Denver Bot — Lyrics search tool via LRCLIB (https://lrclib.net).
"""

import logging
import requests
from langchain_core.tools import tool

logger = logging.getLogger("denver.tools.lyrics")

BASE_URL = "https://lrclib.net/api"

@tool
def lyrics_search(
    query: str = None,
    artist_name: str = None,
    track_name: str = None,
    album_name: str = None,
    synced: bool = False
) -> str:
    """
    Search and retrieve song lyrics using the LRCLIB public API (lrclib.net).
    
    Usage logic:
    - If artist_name + track_name are provided -> searches for the exact result.
    - If only query is provided -> performs a free search and returns a list of results.
    - If synced=True -> returns synchronized lyrics (LRC format) if available.
    
    ALWAYS mention that the source is LRCLIB (lrclib.net).
    """
    try:
        # 1. Exact track match if both artist and track are provided
        if artist_name and track_name:
            params = {
                "artist_name": artist_name,
                "track_name": track_name
            }
            if album_name:
                params["album_name"] = album_name
            
            resp = requests.get(f"{BASE_URL}/get", params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                lyrics_key = "syncedLyrics" if synced else "plainLyrics"
                lyrics = data.get(lyrics_key) or data.get("plainLyrics") or "Letra no disponible."
                
                result = {
                    "found": True,
                    "track": data.get("trackName"),
                    "artist": data.get("artistName"),
                    "album": data.get("albumName"),
                    "duration_seconds": data.get("duration"),
                    "lyrics": lyrics,
                    "synced": synced and bool(data.get("syncedLyrics")),
                    "source": "lrclib.net"
                }
                import json
                return json.dumps(result, indent=2, ensure_ascii=False)
            
            elif resp.status_code == 404:
                return json.dumps({
                    "found": False,
                    "message": "No se encontraron letras exactas para la búsqueda indicada.",
                    "source": "lrclib.net"
                }, indent=2, ensure_ascii=False)
            
            else:
                return f"Error {resp.status_code} consultando LRCLIB."

        # 2. General search if artist/track are incomplete or query is provided
        search_query = query
        if not search_query:
            # Combine available fields for a reasonable search query
            parts = [p for p in [artist_name, track_name, album_name] if p]
            search_query = " ".join(parts)
        
        if not search_query:
            return "Error: Debes proporcionar al menos un término de búsqueda (query, artist_name o track_name)."

        params = {"q": search_query}
        resp = requests.get(f"{BASE_URL}/search", params=params, timeout=10)
        
        if resp.status_code == 200:
            results = resp.json()
            if not results:
                import json
                return json.dumps({
                    "found": False,
                    "message": "No se encontraron resultados para la búsqueda indicada.",
                    "source": "lrclib.net"
                }, indent=2, ensure_ascii=False)
            
            # If 1 result and we had some artist/track info, maybe try to GET it or just return it
            # But LRCLIB search returns an array of objects that already have plainLyrics/syncedLyrics (sometimes)
            # Actually, /search results in LRCLIB DO have plainLyrics/syncedLyrics fields.
            
            # Let's return the first few matches to let the agent/user choose or provide the first one if it looks good.
            # To keep it simple and useful for the agent:
            processed_results = []
            for item in results[:5]:
                processed_results.append({
                    "track": item.get("trackName"),
                    "artist": item.get("artistName"),
                    "album": item.get("albumName"),
                    "duration_seconds": item.get("duration"),
                    "has_synced": bool(item.get("syncedLyrics")),
                    "id": item.get("id")
                })
            
            import json
            return json.dumps({
                "found": True,
                "multiple_results": True,
                "results": processed_results,
                "source": "lrclib.net",
                "message": "Se encontraron varios resultados. Proporciona el artista y canción exactos para obtener la letra, o elige uno de la lista."
            }, indent=2, ensure_ascii=False)
            
        else:
            return f"Error {resp.status_code} realizando búsqueda en LRCLIB."

    except Exception as e:
        logger.error(f"Error en lyrics_search: {e}")
        return f"Error técnico consultando letras: {e}"
