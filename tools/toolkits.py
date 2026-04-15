"""
Denver Bot — Static tool groupings per specialist agent.
Each agent receives only the tools from its domain.
The supervisor never sees these toolkits.
"""

from tools import (
    # Home Assistant — consulta
    get_all_lights_status, get_ha_status, list_ha_entities,
    get_all_sensors_status, get_sensors_by_type, get_weather_forecast,
    capture_camera_image, get_ha_history,
    # Home Assistant — ejecución
    execute_ha_command,
    # Squeezebox / LMS
    squeezebox_call_query, squeezebox_call_method,
    squeezebox_loadtracks, squeezebox_playlist_track_count,
    # Google Calendar
    list_calendar_events, create_calendar_event,
    # Gmail
    list_gmail_messages, send_gmail_message,
    # Contacts
    search_google_contacts,
    # Búsqueda de letras
    lyrics_search,
    # Utilidades
    wikipedia_search, get_current_news,
    calculator, get_current_datetime,
)
from tools.monitors import (
    crear_monitor, listar_monitores, eliminar_monitor, pausar_monitor,
)
from tools.files import (
    write_file, read_file, delete_file, list_files,
)
from tools.scheduler_config import (
    configurar_calendario, configurar_gmail,
    ver_configuracion, configurar_briefing,
)
from tools.memory import (
    guardar_memoria, guardar_regla, consultar_memoria, listar_memorias,
    eliminar_memoria, eliminar_regla, eliminar_todas_las_memorias, configurar_memoria,
)
from tools.websearch import web_search, web_fetch
from tools.vision import analizar_imagen


HOME_AUTOMATION_TOOLKIT = [
    # Home Assistant — consulta
    get_all_lights_status, get_ha_status, list_ha_entities,
    get_all_sensors_status, get_sensors_by_type, get_weather_forecast,
    capture_camera_image, get_ha_history,
    # Home Assistant — ejecución
    execute_ha_command,
    # Squeezebox / LMS
    squeezebox_call_query, squeezebox_call_method,
    squeezebox_loadtracks, squeezebox_playlist_track_count,
    # Monitores
    crear_monitor, listar_monitores, eliminar_monitor, pausar_monitor,
    # Contexto temporal
    get_current_datetime,
    # Memoria (alias de dispositivos)
    guardar_memoria, consultar_memoria,
]

GOOGLE_WORKSPACE_TOOLKIT = [
    # Google Calendar
    list_calendar_events, create_calendar_event,
    # Gmail
    list_gmail_messages, send_gmail_message,
    # Contacts
    search_google_contacts,
    # Configuración de notificaciones y briefing
    configurar_calendario, configurar_gmail, ver_configuracion, configurar_briefing,
    # Memoria (preferencias de contactos o calendarios)
    guardar_memoria, consultar_memoria,
]

UTILITY_TOOLKIT = [
    # Búsqueda e información
    wikipedia_search, get_current_news, lyrics_search,
    web_search, web_fetch,
    # Visión Artificial (Image Analysis)
    analizar_imagen,
    # Cálculo y fecha
    calculator, get_current_datetime,
    # Memoria (gestión completa desde Utility)
    guardar_memoria, guardar_regla, consultar_memoria, listar_memorias, eliminar_memoria, eliminar_regla, eliminar_todas_las_memorias, configurar_memoria,
    # Sistema de archivos
    write_file, read_file, delete_file, list_files,
]
