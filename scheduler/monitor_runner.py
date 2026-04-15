"""
Denver Bot — 4 proactive scheduler jobs with APScheduler.
Each job runs independently — an error in one does not affect the others.
"""

import os
import logging
from datetime import datetime, timedelta

import pytz
import requests
import html
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete, func

from db.database import async_session
from db.models import (
    Monitor, CalendarNotification,
    GmailNotification, UserConfig, BriefingConfig,
)

logger = logging.getLogger("denver.scheduler")

# ─── Module-level trackers ────────────────────────────────────────────────────

_last_run: dict = {
    "monitors": None,
    "calendar": None,
    "gmail": None,
    "briefing": None,
}

_briefing_last_sent: dict[str, datetime] = {}

# ─── HA config ────────────────────────────────────────────────────────────────

HA_URL = os.getenv("HOME_ASSISTANT_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN")
HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}
BRIEFING_TIMEZONE = os.getenv("BRIEFING_TIMEZONE", "America/Argentina/Buenos_Aires")
USER_NAME = os.getenv("USER_NAME", "Damian")


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

async def start_scheduler(bot):
    """Initialize and start the 4 proactive scheduler jobs."""
    # Cleanup old notifications (>7 days)
    try:
        cutoff = datetime.utcnow() - timedelta(days=7)
        async with async_session() as session:
            await session.execute(
                delete(CalendarNotification).where(
                    CalendarNotification.notificado_en < cutoff
                )
            )
            await session.execute(
                delete(GmailNotification).where(
                    GmailNotification.notificado_en < cutoff
                )
            )
            await session.commit()
        logger.info("[Scheduler] Limpieza de notificaciones antiguas completada.")
    except Exception as e:
        logger.warning(f"[Scheduler] Error en limpieza: {e}")

    scheduler = AsyncIOScheduler(timezone=BRIEFING_TIMEZONE)

    interval_monitors = int(os.getenv("SCHEDULER_INTERVAL_MONITORS", "1"))
    interval_calendar = int(os.getenv("SCHEDULER_INTERVAL_CALENDAR", "60"))
    interval_gmail = int(os.getenv("SCHEDULER_INTERVAL_GMAIL", "60"))

    scheduler.add_job(check_ha_monitors, "interval",
                      minutes=interval_monitors, args=[bot])
    scheduler.add_job(check_calendar, "interval",
                      minutes=interval_calendar, args=[bot])
    scheduler.add_job(check_gmail_urgent, "interval",
                      minutes=interval_gmail, args=[bot])
    scheduler.add_job(send_morning_briefing, "interval",
                      minutes=1, args=[bot])

    scheduler.start()
    logger.info(f"[Scheduler] 4 jobs iniciados: monitors({interval_monitors}m), "
                f"calendar({interval_calendar}m), "
                f"gmail({interval_gmail}m), briefing(1m)")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_condition(valor: float, condicion: str,
                        umbral: float = None,
                        val_min: float = None,
                        val_max: float = None) -> bool:
    """Evaluate a numeric condition."""
    if condicion == "mayor_que" and umbral is not None:
        return valor > umbral
    elif condicion == "menor_que" and umbral is not None:
        return valor < umbral
    elif condicion == "igual_a" and umbral is not None:
        return valor == umbral
    elif condicion == "fuera_de_rango" and val_min is not None and val_max is not None:
        return valor < val_min or valor > val_max
    return False


def _get_ha_state_value(entity_id: str) -> tuple[float | None, str]:
    """Get numeric value and unit from an HA entity state."""
    try:
        resp = requests.get(
            f"{HA_URL}/api/states/{entity_id}",
            headers=HA_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return None, ""
        data = resp.json()
        state = data.get("state", "")
        unit = data.get("attributes", {}).get("unit_of_measurement", "")
        try:
            return float(state), unit
        except (ValueError, TypeError):
            return None, unit
    except Exception:
        return None, ""


# ═══════════════════════════════════════════════════════════════════════════════
# JOB 1 — check_ha_monitors
# ═══════════════════════════════════════════════════════════════════════════════

async def check_ha_monitors(bot):
    """Evaluate all active monitors and send notifications when conditions are met."""
    evaluated = 0
    notified = 0
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Monitor).where(Monitor.activo == True)
            )
            monitors = result.scalars().all()

            # Track which monitor IDs need ultima_notificacion updated
            monitors_to_update = []

            for monitor in monitors:
                evaluated += 1
                valor, unidad = _get_ha_state_value(monitor.entidad)
                if valor is None:
                    continue

                if _evaluate_condition(
                    valor, monitor.condicion,
                    umbral=monitor.valor_umbral,
                    val_min=monitor.valor_min,
                    val_max=monitor.valor_max,
                ):
                    # Check cooldown
                    now = datetime.utcnow()
                    if (monitor.ultima_notificacion and
                            (now - monitor.ultima_notificacion).total_seconds() / 60
                            < monitor.intervalo_minutos):
                        continue

                    # Send notification
                    try:
                        await bot.send_message(
                            chat_id=int(monitor.chat_id),
                            text=f"⚠️ {monitor.descripcion or monitor.entidad}: {valor}{unidad}",
                        )
                        notified += 1
                        monitors_to_update.append((monitor.id, now))
                    except Exception as e:
                        logger.error(f"[Scheduler:monitors] Error enviando mensaje: {e}")

            # Batch update all notified monitors in a single commit
            if monitors_to_update:
                for mon_id, ts in monitors_to_update:
                    m_result = await session.execute(
                        select(Monitor).where(Monitor.id == mon_id)
                    )
                    m = m_result.scalar_one_or_none()
                    if m:
                        m.ultima_notificacion = ts
                await session.commit()

        _last_run["monitors"] = datetime.utcnow()
        if evaluated > 0:
            logger.info(f"[Scheduler:monitors] {evaluated} evaluados, {notified} notificaciones enviadas")
    except Exception as e:
        logger.error(f"[Scheduler:monitors] Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# JOB 2 — check_calendar
# ═══════════════════════════════════════════════════════════════════════════════

async def check_calendar(bot):
    """Check upcoming calendar events and send reminders."""
    checked = 0
    reminded = 0
    try:
        from tools import get_google_service

        async with async_session() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.calendar_activo == True)
            )
            configs = result.scalars().all()

        if not configs:
            _last_run["calendar"] = datetime.utcnow()
            return

        service = get_google_service("calendar", "v3")
        if not service:
            logger.warning("[Scheduler:calendar] No hay credenciales de Google.")
            _last_run["calendar"] = datetime.utcnow()
            return

        for config in configs:
            try:
                now = datetime.utcnow()
                time_max = (now + timedelta(hours=24)).isoformat() + "Z"

                events_result = service.events().list(
                    calendarId="primary",
                    timeMin=now.isoformat() + "Z",
                    timeMax=time_max,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()

                events = events_result.get("items", [])
                for event in events:
                    checked += 1
                    event_id = event.get("id", "")
                    start_str = event.get("start", {}).get("dateTime")
                    if not start_str:
                        continue

                    # Parse start time
                    try:
                        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        minutes_until = (start_dt.replace(tzinfo=None) - now).total_seconds() / 60
                    except Exception:
                        continue

                    if 0 < minutes_until <= config.calendar_anticipacion_minutos:
                        # Check if already notified
                        async with async_session() as session:
                            existing = await session.execute(
                                select(CalendarNotification).where(
                                    CalendarNotification.chat_id == config.chat_id,
                                    CalendarNotification.event_id == event_id,
                                )
                            )
                            if existing.scalar_one_or_none():
                                continue

                            # Send reminder
                            title = html.escape(event.get("summary", "Sin título"))
                            mins = int(minutes_until)
                            try:
                                await bot.send_message(
                                    chat_id=int(config.chat_id),
                                    text=f"📅 Recordatorio: <b>{title}</b> en {mins} min",
                                    parse_mode="HTML",
                                )
                                reminded += 1
                            except Exception as e:
                                logger.error(f"[Scheduler:calendar] Error enviando: {e}")

                            # Register notification
                            notif = CalendarNotification(
                                chat_id=config.chat_id,
                                event_id=event_id,
                                notificado_en=now,
                            )
                            session.add(notif)
                            await session.commit()

            except Exception as e:
                logger.error(f"[Scheduler:calendar] Error para chat={config.chat_id}: {e}")

        _last_run["calendar"] = datetime.utcnow()
        logger.info(f"[Scheduler:calendar] {checked} eventos chequeados, {reminded} recordatorios enviados")
    except Exception as e:
        logger.error(f"[Scheduler:calendar] Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# JOB 4 — check_gmail_urgent
# ═══════════════════════════════════════════════════════════════════════════════

async def check_gmail_urgent(bot):
    """Check for urgent unread emails and notify. Uses batch LLM evaluation."""
    processed = 0
    notified = 0
    try:
        from tools import get_google_service
        from graph import get_llm
        from langchain_core.messages import HumanMessage

        async with async_session() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.gmail_activo == True)
            )
            configs = result.scalars().all()

        if not configs:
            _last_run["gmail"] = datetime.utcnow()
            return

        gmail_service = get_google_service("gmail", "v1")
        if not gmail_service:
            logger.warning("[Scheduler:gmail] No hay credenciales de Google.")
            _last_run["gmail"] = datetime.utcnow()
            return

        for config in configs:
            # Check pause
            now = datetime.utcnow()
            if config.gmail_pausa_hasta and config.gmail_pausa_hasta > now:
                continue

            try:
                results = gmail_service.users().messages().list(
                    userId="me", q="is:unread", maxResults=10
                ).execute()

                messages = results.get("messages", [])
                
                # Phase 1: Collect unprocessed emails (single DB session for dedup)
                emails_to_evaluate = []
                async with async_session() as session:
                    for msg_meta in messages:
                        processed += 1
                        msg_id = msg_meta["id"]

                        existing = await session.execute(
                            select(GmailNotification).where(
                                GmailNotification.chat_id == config.chat_id,
                                GmailNotification.message_id == msg_id,
                            )
                        )
                        if existing.scalar_one_or_none():
                            continue

                        # Get message details
                        msg = gmail_service.users().messages().get(
                            userId="me", id=msg_id, format="metadata",
                            metadataHeaders=["From", "Subject"]
                        ).execute()

                        headers = {h["name"]: h["value"]
                                   for h in msg.get("payload", {}).get("headers", [])}
                        emails_to_evaluate.append({
                            "id": msg_id,
                            "sender": headers.get("From", "Desconocido"),
                            "subject": headers.get("Subject", "Sin asunto"),
                            "snippet": msg.get("snippet", "")[:200],
                        })

                if not emails_to_evaluate:
                    continue

                # Phase 2: Batch LLM urgency evaluation (single call for all emails)
                urgent_ids = set()
                try:
                    llm = get_llm(temperature=0)
                    email_lines = []
                    for i, em in enumerate(emails_to_evaluate, 1):
                        email_lines.append(
                            f"{i}. From: {em['sender']} | Subject: {em['subject']} | Snippet: {em['snippet']}"
                        )
                    batch_prompt = (
                        "Classify each email as URGENT or NOT. "
                        "Criteria: known sender, keywords (urgent, important, meeting, payment, "
                        "due, action required, deadline, invoice).\n"
                        "Respond with ONLY the numbers of urgent emails, comma-separated. "
                        "If none are urgent, respond with NONE.\n\n"
                        + "\n".join(email_lines)
                    )
                    batch_result = await llm.ainvoke([HumanMessage(content=batch_prompt)])
                    response_text = batch_result.content.strip().upper()
                    
                    if "NONE" not in response_text:
                        import re
                        numbers = re.findall(r'\d+', response_text)
                        for num_str in numbers:
                            idx = int(num_str) - 1
                            if 0 <= idx < len(emails_to_evaluate):
                                urgent_ids.add(emails_to_evaluate[idx]["id"])
                except Exception as e:
                    logger.error(f"[Scheduler:gmail] Error consultando LLM: {e}")

                # Phase 3: Notify urgent emails and register all in DB (single session)
                async with async_session() as session:
                    for em in emails_to_evaluate:
                        if em["id"] in urgent_ids:
                            try:
                                s_esc = html.escape(em["sender"])
                                sub_esc = html.escape(em["subject"])
                                await bot.send_message(
                                    chat_id=int(config.chat_id),
                                    text=f"📧 Mail importante de <b>{s_esc}</b>: {sub_esc}",
                                    parse_mode="HTML",
                                )
                                notified += 1
                            except Exception as e:
                                logger.error(f"[Scheduler:gmail] Error enviando: {e}")

                        # Register in DB (even if not urgent, to avoid re-processing)
                        notif = GmailNotification(
                            chat_id=config.chat_id,
                            message_id=em["id"],
                            notificado_en=datetime.utcnow(),
                        )
                        session.add(notif)
                    await session.commit()

            except Exception as e:
                logger.error(f"[Scheduler:gmail] Error para chat={config.chat_id}: {e}")

        _last_run["gmail"] = datetime.utcnow()
        logger.info(f"[Scheduler:gmail] {processed} mails procesados, {notified} urgentes notificados")
    except Exception as e:
        logger.error(f"[Scheduler:gmail] Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# JOB 5 — send_morning_briefing
# ═══════════════════════════════════════════════════════════════════════════════

async def send_morning_briefing(bot):
    """Send morning briefing at the configured hour. Runs every 1 minute."""
    try:
        tz = pytz.timezone(BRIEFING_TIMEZONE)
        ahora = datetime.now(tz)

        async with async_session() as session:
            result = await session.execute(
                select(BriefingConfig).where(BriefingConfig.activo == True)
            )
            configs = result.scalars().all()

        for config in configs:
            # Check hour: at or after config.hora, but before 12:00
            if ahora.hour < config.hora or ahora.hour >= 12:
                continue

            # Check if already sent today (using persistent DB column)
            if config.ultimo_envio:
                # El record de DB es naive UTC. Lo hacemos aware antes de comparar.
                last_sent_utc = config.ultimo_envio.replace(tzinfo=pytz.utc)
                if last_sent_utc.astimezone(tz).date() == ahora.date():
                    continue

            # Build briefing message — USE HTML for better robustness with _ in names
            dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            dia = dias[ahora.weekday()]
            fecha = ahora.strftime("%d/%m/%Y")
            user_esc = html.escape(USER_NAME)
            mensaje = f"🌅 <b>Buenos días {user_esc}</b> — {dia} {fecha}\n\n"

            # CLIMA
            if config.incluir_clima:
                try:
                    # Dynamic weather discovery
                    weather_eid = None 
                    from tools import get_ha_entities
                    entities = get_ha_entities()
                    weather_entities = [e for e in entities if e.get("entity_id", "").startswith("weather.")]
                    if weather_entities:
                        weather_eid = weather_entities[0]["entity_id"]
                    
                    if not weather_eid:
                        mensaje += "🌤️ Clima: ❌ No se encontró entidad de clima\n\n"
                        continue

                    resp = requests.get(
                        f"{HA_URL}/api/states/{weather_eid}",
                        headers=HA_HEADERS, timeout=10,
                    )
                    # Auto-discovery if default not found
                    if resp.status_code == 404:
                        all_resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
                        if all_resp.status_code == 200:
                            weather_entities = [e for e in all_resp.json() if e.get("entity_id", "").startswith("weather.")]
                            if weather_entities:
                                weather_eid = weather_entities[0]["entity_id"]
                                resp = requests.get(f"{HA_URL}/api/states/{weather_eid}", headers=HA_HEADERS, timeout=10)

                    if resp.status_code == 200:
                        data = resp.json()
                        attrs = data.get("attributes", {})
                        from tools import WEATHER_STATES
                        cond = WEATHER_STATES.get(data.get("state", ""), data.get("state", ""))
                        temp = attrs.get("temperature", "?")
                        forecast = attrs.get("forecast", [])
                        fcast = ""
                        if forecast:
                            f0 = forecast[0]
                            fcast = f" | Máx {f0.get('temperature', '?')}° / Mín {f0.get('templow', '?')}°"
                        mensaje += f"🌤️ Clima ({weather_eid}): {cond}, {temp}°C{fcast}\n\n"
                    else:
                        mensaje += "🌤️ Clima: ❌ No disponible\n\n"
                except Exception as e:
                    logger.error(f"[Scheduler:briefing] Error con clima: {e}")
                    mensaje += "🌤️ Clima: ❌ No disponible\n\n"

            # CALENDARIO
            if config.incluir_calendario:
                try:
                    from tools import get_google_service
                    cal_service = get_google_service("calendar", "v3")
                    if cal_service:
                        # Events for today
                        start_of_day = ahora.replace(hour=0, minute=0, second=0).astimezone(pytz.utc)
                        end_of_day = ahora.replace(hour=23, minute=59, second=59).astimezone(pytz.utc)

                        events_result = cal_service.events().list(
                            calendarId="primary",
                            timeMin=start_of_day.isoformat(),
                            timeMax=end_of_day.isoformat(),
                            maxResults=10,
                            singleEvents=True,
                            orderBy="startTime",
                        ).execute()

                        events = events_result.get("items", [])
                        if events:
                            mensaje += "📅 <b>Agenda:</b>\n"
                            for evt in events:
                                start = evt.get("start", {}).get("dateTime", "")
                                title = html.escape(evt.get("summary", "Sin título"))
                                hora_evt = start[11:16] if len(start) > 16 else "Todo el día"
                                mensaje += f"  • {hora_evt} — {title}\n"
                            mensaje += "\n"
                        else:
                            mensaje += "📅 Sin eventos hoy\n\n"
                    else:
                        mensaje += "📅 Calendario: ❌ No disponible\n\n"
                except Exception:
                    mensaje += "📅 Calendario: ❌ No disponible\n\n"

            # GMAIL
            if config.incluir_gmail:
                try:
                    from tools import get_google_service
                    gmail_service = get_google_service("gmail", "v1")
                    if gmail_service:
                        # Accurate unread count in Inbox
                        inbox_label = gmail_service.users().labels().get(
                            userId="me", id="INBOX"
                        ).execute()
                        unread_count = inbox_label.get("messagesUnread", 0)
                        
                        mensaje += f"📧 {unread_count} mails sin leer en Inbox\n"
                        
                        if unread_count > 0:
                            # Show latest 5 unread subjects
                            list_results = gmail_service.users().messages().list(
                                userId="me", q="label:INBOX is:unread", maxResults=5
                            ).execute()
                            messages = list_results.get("messages", [])
                            for m_meta in messages:
                                m = gmail_service.users().messages().get(
                                    userId="me", id=m_meta["id"], format="metadata",
                                    metadataHeaders=["From", "Subject"]
                                ).execute()
                                m_headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
                                m_sub = html.escape(m_headers.get("Subject", "Sin asunto"))
                                m_from = html.escape(m_headers.get("From", "Desconocido").split("<")[0].strip())
                                mensaje += f"  • <b>{m_from}</b>: {m_sub}\n"
                            mensaje += "\n"
                        else:
                            mensaje += "\n"
                    else:
                        mensaje += "📧 Gmail: ❌ No disponible\n\n"
                except Exception:
                    mensaje += "📧 Gmail: ❌ No disponible\n\n"

            # HOGAR
            if config.incluir_hogar:
                try:
                    resp = requests.get(
                        f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10,
                    )
                    if resp.status_code == 200:
                        states = resp.json()
                        lights_on = sum(
                            1 for s in states
                            if s.get("entity_id", "").startswith("light.")
                            and s.get("state") == "on"
                        )
                        # Find a temperature sensor
                        temp_info = ""
                        for s in states:
                            if (s.get("entity_id", "").startswith("sensor.") and
                                    s.get("attributes", {}).get("device_class") == "temperature"):
                                temp_val = s.get("state", "?")
                                temp_info = f" | Temp: {temp_val}°C"
                                break
                        mensaje += f"🏠 {lights_on} luces encendidas{temp_info}\n"
                    else:
                        mensaje += "🏠 Hogar: ❌ No disponible\n"
                except Exception:
                    mensaje += "🏠 Hogar: ❌ No disponible\n"

            # Send briefing
            try:
                await bot.send_message(
                    chat_id=int(config.chat_id),
                    text=mensaje,
                    parse_mode="HTML",
                )
                
                # Update persistent last sent time IN DATABASE
                async with async_session() as session:
                    result = await session.execute(
                        select(BriefingConfig).where(BriefingConfig.chat_id == config.chat_id)
                    )
                    bc = result.scalar_one_or_none()
                    if bc:
                        bc.ultimo_envio = ahora
                        await session.commit()
                
                _briefing_last_sent[config.chat_id] = ahora
                _last_run["briefing"] = datetime.utcnow()
                logger.info(f"[Scheduler:briefing] Enviado a chat_id={config.chat_id}")
            except Exception as e:
                logger.error(f"[Scheduler:briefing] Error enviando: {e}")

    except Exception as e:
        logger.error(f"[Scheduler:briefing] Error: {e}")
