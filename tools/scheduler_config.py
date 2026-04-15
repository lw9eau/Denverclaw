"""
Denver Bot — Async tools for proactive notification and briefing configuration.
"""

import logging
from datetime import datetime
from langchain_core.tools import tool
from sqlalchemy import select
from db.database import async_session
from db.models import UserConfig, BriefingConfig

logger = logging.getLogger("denver.tools.scheduler_config")


@tool
async def configurar_calendario(chat_id: str, activo: bool,
                                  anticipacion_minutos: int = 60) -> str:
    """
    Activates or deactivates automatic Google Calendar notifications.
    anticipacion_minutos: how far in advance to send the reminder (default 60 min).
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.chat_id == chat_id)
            )
            config = result.scalar_one_or_none()

            if config:
                config.calendar_activo = activo
                config.calendar_anticipacion_minutos = anticipacion_minutos
            else:
                config = UserConfig(
                    chat_id=chat_id,
                    calendar_activo=activo,
                    calendar_anticipacion_minutos=anticipacion_minutos,
                )
                session.add(config)

            await session.commit()

        status = "activadas ✅" if activo else "desactivadas ❌"
        return (f"Notificaciones de calendario {status}.\n"
                f"Anticipación: {anticipacion_minutos} minutos.")
    except Exception as e:
        return f"Error configurando calendario: {e}"


@tool
async def configurar_gmail(chat_id: str, activo: bool,
                             pausa_hasta: str = None) -> str:
    """
    Activates or deactivates notifications for urgent emails.
    pausa_hasta: ISO datetime to pause only until that moment
    (e.g., "2025-03-15T08:00:00" to not disturb tonight).
    """
    try:
        pausa_dt = None
        if pausa_hasta:
            pausa_dt = datetime.fromisoformat(pausa_hasta)

        async with async_session() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.chat_id == chat_id)
            )
            config = result.scalar_one_or_none()

            if config:
                config.gmail_activo = activo
                config.gmail_pausa_hasta = pausa_dt
            else:
                config = UserConfig(
                    chat_id=chat_id,
                    gmail_activo=activo,
                    gmail_pausa_hasta=pausa_dt,
                )
                session.add(config)

            await session.commit()

        status = "activadas ✅" if activo else "desactivadas ❌"
        pausa_info = f"\nPausado hasta: {pausa_hasta}" if pausa_hasta else ""
        return f"Notificaciones de Gmail {status}.{pausa_info}"
    except Exception as e:
        return f"Error configurando Gmail: {e}"


@tool
async def ver_configuracion(chat_id: str) -> str:
    """Shows the current configuration of all the user's proactive notifications."""
    try:
        async with async_session() as session:
            # UserConfig
            uc_result = await session.execute(
                select(UserConfig).where(UserConfig.chat_id == chat_id)
            )
            uc = uc_result.scalar_one_or_none()

            # BriefingConfig
            bc_result = await session.execute(
                select(BriefingConfig).where(BriefingConfig.chat_id == chat_id)
            )
            bc = bc_result.scalar_one_or_none()

        lines = ["⚙️ Configuración de notificaciones:\n"]

        # Calendar
        if uc:
            cal_status = "✅ Activo" if uc.calendar_activo else "❌ Desactivado"
            lines.append(f"📅 Calendario: {cal_status} (anticipación: {uc.calendar_anticipacion_minutos} min)")
            gmail_status = "✅ Activo" if uc.gmail_activo else "❌ Desactivado"
            pausa = f" (pausado hasta {uc.gmail_pausa_hasta})" if uc.gmail_pausa_hasta else ""
            lines.append(f"📧 Gmail: {gmail_status}{pausa}")
        else:
            lines.append("📅 Calendario: ✅ Activo (config por defecto)")
            lines.append("📧 Gmail: ✅ Activo (config por defecto)")

        # Briefing
        if bc:
            br_status = "✅ Activo" if bc.activo else "❌ Desactivado"
            secciones = []
            if bc.incluir_clima:
                secciones.append("clima")
            if bc.incluir_calendario:
                secciones.append("calendario")
            if bc.incluir_gmail:
                secciones.append("gmail")
            if bc.incluir_hogar:
                secciones.append("hogar")
            lines.append(f"🌅 Briefing: {br_status} a las {bc.hora}:00 ({', '.join(secciones)})")
        else:
            lines.append("🌅 Briefing: ❌ No configurado")

        return "\n".join(lines)
    except Exception as e:
        return f"Error consultando configuración: {e}"


@tool
async def configurar_briefing(chat_id: str, activo: bool, hora: int = 7,
                               incluir_clima: bool = True,
                               incluir_calendario: bool = True,
                               incluir_gmail: bool = True,
                               incluir_hogar: bool = True) -> str:
    """
    Configures the automatic morning briefing.
    hora: local delivery time in 24h format (0–23). Default: 7 (7:00 AM).
    Each section is optional and independent.
    Example: "Send me the briefing at 8 AM only with weather, calendar, and Gmail"
             → configurar_briefing(hora=8, incluir_gmail=True, incluir_hogar=False)
    """
    try:
        if hora < 0 or hora > 23:
            return "La hora debe estar entre 0 y 23."

        async with async_session() as session:
            result = await session.execute(
                select(BriefingConfig).where(BriefingConfig.chat_id == chat_id)
            )
            config = result.scalar_one_or_none()

            if config:
                config.activo = activo
                config.hora = hora
                config.incluir_clima = incluir_clima
                config.incluir_calendario = incluir_calendario
                config.incluir_gmail = incluir_gmail
                config.incluir_hogar = incluir_hogar
            else:
                config = BriefingConfig(
                    chat_id=chat_id,
                    activo=activo,
                    hora=hora,
                    incluir_clima=incluir_clima,
                    incluir_calendario=incluir_calendario,
                    incluir_gmail=incluir_gmail,
                    incluir_hogar=incluir_hogar,
                )
                session.add(config)

            await session.commit()

        status = "activado ✅" if activo else "desactivado ❌"
        secciones = []
        if incluir_clima:
            secciones.append("clima")
        if incluir_calendario:
            secciones.append("calendario")
        if incluir_gmail:
            secciones.append("gmail")
        if incluir_hogar:
            secciones.append("hogar")

        return (f"🌅 Briefing {status}\n"
                f"  Hora: {hora}:00\n"
                f"  Secciones: {', '.join(secciones)}")
    except Exception as e:
        return f"Error configurando briefing: {e}"
