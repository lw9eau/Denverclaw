"""
Denver Bot — Test completo de todas las tools.
Ejecuta cada tool individualmente sin pasar por el LLM.
Skippea tools cuyo servicio externo no esté disponible.

Uso: python test_tools.py
"""

import os
import sys
import asyncio
import json
import time
from dotenv import load_dotenv

load_dotenv()

# Fix Windows console encoding for emojis
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ─── Color helpers ────────────────────────────────────────────────────────────

def green(text): return f"\033[92m✅ {text}\033[0m"
def red(text): return f"\033[91m❌ {text}\033[0m"
def yellow(text): return f"\033[93m⚠️  {text}\033[0m"
def cyan(text): return f"\033[96m{text}\033[0m"
def bold(text): return f"\033[1m{text}\033[0m"

# ─── Results tracker ──────────────────────────────────────────────────────────

results = {"pass": [], "fail": [], "skip": []}

def record(name, status, detail=""):
    results[status].append((name, detail))
    if status == "pass":
        print(f"  {green(name)}" + (f"  → {detail[:80]}" if detail else ""))
    elif status == "fail":
        print(f"  {red(name)}  → {detail[:120]}")
    else:
        print(f"  {yellow(name)}  → {detail[:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
# CONNECTIVITY CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_ha_connectivity():
    """Check if Home Assistant is reachable."""
    import requests
    ha_url = os.getenv("HOME_ASSISTANT_URL", "").rstrip("/")
    ha_token = os.getenv("HOME_ASSISTANT_TOKEN")
    if not ha_url or not ha_token:
        return False, "HOME_ASSISTANT_URL o TOKEN no configurados"
    try:
        resp = requests.get(f"{ha_url}/api/", 
                           headers={"Authorization": f"Bearer {ha_token}"},
                           timeout=5)
        return resp.status_code == 200, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def check_google_connectivity():
    """Check if Google token.json exists and is valid."""
    if not os.path.exists("token.json"):
        return False, "token.json no encontrado"
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file("token.json")
        if creds.valid:
            return True, "token válido"
        elif creds.expired and creds.refresh_token:
            return True, "token expirado pero tiene refresh_token"
        return False, "token inválido"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC TOOLS TESTS (tools/__init__.py)
# ═══════════════════════════════════════════════════════════════════════════════

def test_sync_tools():
    print(f"\n{bold('═' * 60)}")
    print(f"{bold('  SYNC TOOLS — tools/__init__.py')}")
    print(f"{bold('═' * 60)}")

    from tools import (
        get_all_lights_status, get_ha_status, list_ha_entities,
        get_all_sensors_status, get_sensors_by_type, get_weather_forecast,
        capture_camera_image, execute_ha_command,
        squeezebox_call_query, squeezebox_call_method,
        squeezebox_loadtracks, squeezebox_playlist_track_count,
        list_calendar_events, create_calendar_event,
        list_gmail_messages, send_gmail_message, search_google_contacts,
        wikipedia_search, get_current_news, calculator, get_current_datetime,
        get_google_service,
    )

    # ─── Connectivity ─────────────────────────────────────────────────────

    ha_ok, ha_detail = check_ha_connectivity()
    google_ok, google_detail = check_google_connectivity()

    print(f"\n  {cyan('Conectividad:')}")
    print(f"    Home Assistant: {'✅' if ha_ok else '❌'} {ha_detail}")
    print(f"    Google APIs: {'✅' if google_ok else '❌'} {google_detail}")

    # ─── HA Query Tools ───────────────────────────────────────────────────

    print(f"\n  {cyan('Home Assistant — Consultas:')}")

    if ha_ok:
        # get_current_datetime (no deps)
        try:
            result = get_current_datetime.invoke({})
            record("get_current_datetime", "pass", result[:60])
        except Exception as e:
            record("get_current_datetime", "fail", str(e))

        # get_all_lights_status
        try:
            result = get_all_lights_status.invoke({})
            record("get_all_lights_status", "pass", result[:60])
        except Exception as e:
            record("get_all_lights_status", "fail", str(e))

        # list_ha_entities (with domain filter)
        try:
            result = list_ha_entities.invoke({"domain": "light"})
            count = result.count("•")
            record("list_ha_entities(domain='light')", "pass", f"{count} entidades encontradas")
        except Exception as e:
            record("list_ha_entities", "fail", str(e))

        # list_ha_entities (with search_term)
        try:
            result = list_ha_entities.invoke({"domain": "camera"})
            count = result.count("•")
            record("list_ha_entities(domain='camera')", "pass", f"{count} cámaras encontradas")
        except Exception as e:
            record("list_ha_entities(domain='camera')", "fail", str(e))

        # get_ha_status (pick first light found)
        try:
            ents = list_ha_entities.invoke({"domain": "light"})
            # Extract first entity_id
            import re
            match = re.search(r'(light\.\w+)', ents)
            if match:
                eid = match.group(1)
                result = get_ha_status.invoke({"entity_id": eid})
                record(f"get_ha_status('{eid}')", "pass", result[:60])
            else:
                record("get_ha_status", "skip", "No light entities found")
        except Exception as e:
            record("get_ha_status", "fail", str(e))

        # get_all_sensors_status
        try:
            result = get_all_sensors_status.invoke({})
            record("get_all_sensors_status", "pass", result[:60])
        except Exception as e:
            record("get_all_sensors_status", "fail", str(e))

        # get_sensors_by_type
        try:
            result = get_sensors_by_type.invoke({"sensor_type": "temperature"})
            record("get_sensors_by_type('temperature')", "pass", result[:60])
        except Exception as e:
            record("get_sensors_by_type", "fail", str(e))

        # get_weather_forecast
        try:
            result = get_weather_forecast.invoke({})
            record("get_weather_forecast", "pass", result[:60])
        except Exception as e:
            record("get_weather_forecast", "fail", str(e))

        # capture_camera_image (find first camera)
        try:
            cams = list_ha_entities.invoke({"domain": "camera"})
            match = re.search(r'(camera\.\w+)', cams)
            if match:
                cid = match.group(1)
                result = capture_camera_image.invoke({"entity_id": cid})
                from tools import _captured_image
                has_image = _captured_image is not None
                record(f"capture_camera_image('{cid}')", "pass",
                       f"{result[:50]} | image_bytes={'sí' if has_image else 'no'}")
            else:
                record("capture_camera_image", "skip", "No cameras found")
        except Exception as e:
            record("capture_camera_image", "fail", str(e))

        # execute_ha_command (read-only: get state via services — skip destructive)
        record("execute_ha_command", "skip", "Skipped (test destructivo — requiere entidad real)")

    else:
        for tool_name in ["get_all_lights_status", "list_ha_entities", "get_ha_status",
                          "get_all_sensors_status", "get_sensors_by_type",
                          "get_weather_forecast", "capture_camera_image",
                          "execute_ha_command"]:
            record(tool_name, "skip", f"HA no disponible: {ha_detail}")

    # ─── Squeezebox Tools ─────────────────────────────────────────────────

    print(f"\n  {cyan('Squeezebox / LMS:')}")

    if ha_ok:
        # Check if media_player exists for squeezebox
        try:
            mp = list_ha_entities.invoke({"domain": "media_player", "search_term": "entrepiso"})
            has_squeeze = "media_player." in mp
        except Exception:
            has_squeeze = False

        if has_squeeze:
            try:
                result = squeezebox_call_query.invoke({"entity_id": "media_player.reproductor_entrepiso", "command": "status", "parameters": []})
                record("squeezebox_call_query", "pass", result[:60])
            except Exception as e:
                record("squeezebox_call_query", "fail", str(e))

            try:
                result = squeezebox_playlist_track_count.invoke({"entity_id": "media_player.reproductor_entrepiso"})
                record("squeezebox_playlist_track_count", "pass", result[:60])
            except Exception as e:
                record("squeezebox_playlist_track_count", "fail", str(e))

            record("squeezebox_call_method", "skip", "Skipped (test destructivo)")
            record("squeezebox_loadtracks", "skip", "Skipped (test destructivo)")
        else:
            for t in ["squeezebox_call_query", "squeezebox_call_method",
                       "squeezebox_loadtracks", "squeezebox_playlist_track_count"]:
                record(t, "skip", "No Squeezebox media_player found")
    else:
        for t in ["squeezebox_call_query", "squeezebox_call_method",
                   "squeezebox_loadtracks", "squeezebox_playlist_track_count"]:
            record(t, "skip", f"HA no disponible: {ha_detail}")

    # ─── Google APIs ──────────────────────────────────────────────────────

    print(f"\n  {cyan('Google APIs:')}")

    if google_ok:
        # Calendar
        try:
            result = list_calendar_events.invoke({})
            record("list_calendar_events", "pass", result[:60])
        except Exception as e:
            record("list_calendar_events", "fail", str(e))

        record("create_calendar_event", "skip", "Skipped (test destructivo)")

        # Gmail
        try:
            result = list_gmail_messages.invoke({})
            record("list_gmail_messages", "pass", result[:60])
        except Exception as e:
            record("list_gmail_messages", "fail", str(e))

        record("send_gmail_message", "skip", "Skipped (test destructivo)")

        # Contacts
        user_name = os.getenv("USER_NAME", "Damian")
        try:
            result = search_google_contacts.invoke({"query": user_name})
            record(f"search_google_contacts('{user_name}')", "pass", result[:60])
        except Exception as e:
            record("search_google_contacts", "fail", str(e))
    else:
        for t in ["list_calendar_events", "create_calendar_event",
                   "list_gmail_messages", "send_gmail_message", "search_google_contacts"]:
            record(t, "skip", f"Google no disponible: {google_detail}")

    # ─── Utility Tools ────────────────────────────────────────────────────

    print(f"\n  {cyan('Utilidades:')}")

    # get_current_datetime (already tested above if HA ok, test again standalone)
    try:
        result = get_current_datetime.invoke({})
        record("get_current_datetime", "pass", result[:60])
    except Exception as e:
        record("get_current_datetime", "fail", str(e))

    # calculator
    try:
        result = calculator.invoke({"expression": "2**10 + sqrt(144)"})
        record("calculator('2**10 + sqrt(144)')", "pass", result)
    except Exception as e:
        record("calculator", "fail", str(e))

    # wikipedia_search
    try:
        result = wikipedia_search.invoke({"query": "Buenos Aires"})
        record("wikipedia_search('Buenos Aires')", "pass", f"{len(result)} chars")
    except Exception as e:
        record("wikipedia_search", "fail", str(e))

    # get_current_news
    try:
        result = get_current_news.invoke({})
        record("get_current_news", "pass", f"{len(result)} chars")
    except Exception as e:
        record("get_current_news", "fail", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC TOOLS TESTS (monitors, scheduler_config, memory)
# ═══════════════════════════════════════════════════════════════════════════════

async def test_async_tools():
    from db.database import init_db
    await init_db()

    print(f"\n{bold('═' * 60)}")
    print(f"{bold('  ASYNC TOOLS — monitors.py')}")
    print(f"{bold('═' * 60)}")

    from tools.monitors import (
        crear_monitor, listar_monitores, eliminar_monitor, pausar_monitor,
    )

    test_chat_id = "TEST_000"
    monitor_id = None

    # crear_monitor
    print(f"\n  {cyan('Monitores CRUD:')}")
    try:
        result = await crear_monitor.ainvoke({
            "chat_id": test_chat_id,
            "entidad": "sensor.test_temperature",
            "condicion": "mayor_que",
            "valor_umbral": 30.0,
            "descripcion": "Test: temp > 30",
        })
        record("crear_monitor", "pass", result[:60])
        # Extract ID
        import re
        m = re.search(r'#(\d+)', result)
        if m:
            monitor_id = int(m.group(1))
    except Exception as e:
        record("crear_monitor", "fail", str(e))

    # listar_monitores
    try:
        result = await listar_monitores.ainvoke({"chat_id": test_chat_id})
        record("listar_monitores", "pass", result[:60])
    except Exception as e:
        record("listar_monitores", "fail", str(e))

    # pausar_monitor
    if monitor_id:
        try:
            result = await pausar_monitor.ainvoke({
                "chat_id": test_chat_id,
                "monitor_id": monitor_id,
            })
            record(f"pausar_monitor({monitor_id})", "pass", result[:60])
        except Exception as e:
            record("pausar_monitor", "fail", str(e))
    else:
        record("pausar_monitor", "skip", "No monitor_id from crear_monitor")

    # eliminar_monitor
    if monitor_id:
        try:
            result = await eliminar_monitor.ainvoke({
                "chat_id": test_chat_id,
                "monitor_id": monitor_id,
            })
            record(f"eliminar_monitor({monitor_id})", "pass", result[:60])
        except Exception as e:
            record("eliminar_monitor", "fail", str(e))
    else:
        record("eliminar_monitor", "skip", "No monitor_id")



    # ─── Scheduler Config ─────────────────────────────────────────────────

    print(f"\n{bold('═' * 60)}")
    print(f"{bold('  ASYNC TOOLS — scheduler_config.py')}")
    print(f"{bold('═' * 60)}")

    from tools.scheduler_config import (
        configurar_calendario, configurar_gmail,
        configurar_briefing, ver_configuracion,
    )

    print(f"\n  {cyan('Configuración de notificaciones:')}")

    try:
        result = await configurar_calendario.ainvoke({
            "chat_id": test_chat_id,
            "activo": True,
            "anticipacion_minutos": 15,
        })
        record("configurar_calendario", "pass", result[:60])
    except Exception as e:
        record("configurar_calendario", "fail", str(e))

    try:
        result = await configurar_gmail.ainvoke({
            "chat_id": test_chat_id,
            "activo": True,
        })
        record("configurar_gmail", "pass", result[:60])
    except Exception as e:
        record("configurar_gmail", "fail", str(e))

    try:
        result = await configurar_briefing.ainvoke({
            "chat_id": test_chat_id,
            "activo": True,
            "hora": 8,
        })
        record("configurar_briefing", "pass", result[:60])
    except Exception as e:
        record("configurar_briefing", "fail", str(e))

    try:
        result = await ver_configuracion.ainvoke({"chat_id": test_chat_id})
        record("ver_configuracion", "pass", result[:60])
    except Exception as e:
        record("ver_configuracion", "fail", str(e))

    # ─── Memory ───────────────────────────────────────────────────────────

    print(f"\n{bold('═' * 60)}")
    print(f"{bold('  ASYNC TOOLS — memory.py')}")
    print(f"{bold('═' * 60)}")

    from tools.memory import (
        guardar_memoria, consultar_memoria, listar_memorias,
        eliminar_memoria, configurar_memoria,
    )

    print(f"\n  {cyan('Memoria persistente:')}")

    try:
        result = await guardar_memoria.ainvoke({
            "chat_id": test_chat_id,
            "clave": "test_color_favorito",
            "valor": "azul",
            "descripcion": "Color favorito para tests",
        })
        record("guardar_memoria", "pass", result[:60])
    except Exception as e:
        record("guardar_memoria", "fail", str(e))

    try:
        result = await consultar_memoria.ainvoke({
            "chat_id": test_chat_id,
            "clave": "test_color_favorito",
        })
        record("consultar_memoria", "pass", result[:60])
    except Exception as e:
        record("consultar_memoria", "fail", str(e))

    try:
        result = await listar_memorias.ainvoke({"chat_id": test_chat_id})
        record("listar_memorias", "pass", result[:60])
    except Exception as e:
        record("listar_memorias", "fail", str(e))

    try:
        result = await eliminar_memoria.ainvoke({
            "chat_id": test_chat_id,
            "clave": "test_color_favorito",
        })
        record("eliminar_memoria", "pass", result[:60])
    except Exception as e:
        record("eliminar_memoria", "fail", str(e))

    try:
        result = await configurar_memoria.ainvoke({
            "chat_id": test_chat_id,
            "activa": True,
        })
        record("configurar_memoria", "pass", result[:60])
    except Exception as e:
        record("configurar_memoria", "fail", str(e))

    # ─── Cleanup test data ────────────────────────────────────────────────

    print(f"\n  {cyan('Limpieza de datos de test...')}")
    try:
        from db.database import async_session
        from db.models import (Monitor, UserConfig,
                               BriefingConfig, Memoria, MemoriaConfig)
        from sqlalchemy import delete

        async with async_session() as session:
            for model in [Monitor, UserConfig, BriefingConfig,
                          Memoria, MemoriaConfig]:
                await session.execute(
                    delete(model).where(model.chat_id == test_chat_id)
                )
            await session.commit()
        print(f"  {green('Cleanup')}  → datos de TEST_000 eliminados")
    except Exception as e:
        print(f"  {red('Cleanup')}  → {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{bold('🤖 Denver Bot — Test completo de tools')}")
    print(f"{bold('=' * 60)}")

    start = time.time()

    # Sync tools
    test_sync_tools()

    # Async tools
    asyncio.run(test_async_tools())

    elapsed = time.time() - start

    # ─── Summary ──────────────────────────────────────────────────────────

    print(f"\n{bold('=' * 60)}")
    print(f"{bold('  RESUMEN')}")
    print(f"{bold('=' * 60)}")

    total = len(results["pass"]) + len(results["fail"]) + len(results["skip"])
    n_pass = len(results["pass"])
    n_fail = len(results["fail"])
    n_skip = len(results["skip"])
    print(f"\n  Total: {total} tests")
    print(f"  {green(f'{n_pass} passed')}")
    if results["fail"]:
        print(f"  {red(f'{n_fail} failed')}")
        for name, detail in results["fail"]:
            print(f"    → {name}: {detail[:100]}")
    if results["skip"]:
        print(f"  {yellow(f'{n_skip} skipped')}")
    print(f"\n  Tiempo: {elapsed:.1f}s\n")

    # Exit code
    sys.exit(1 if results["fail"] else 0)


if __name__ == "__main__":
    main()
