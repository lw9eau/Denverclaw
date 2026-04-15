"""
Denver Bot — Async tools for CRUD of monitors.
These tools are async natives — no need for asyncio.to_thread().
"""

import logging
from datetime import datetime
from langchain_core.tools import tool
from sqlalchemy import select
from db.database import async_session
from db.models import Monitor

logger = logging.getLogger("denver.tools.monitors")


# ═══════════════════════════════════════════════════════════════════════════════
# MONITORES
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def crear_monitor(chat_id: str, descripcion: str, entidad: str,
                         condicion: str, valor_min: float = None,
                         valor_max: float = None, valor_umbral: float = None,
                         intervalo_minutos: int = 10) -> str:
    """
    Creates a proactive monitoring rule for a Home Assistant entity.
    Denver will notify automatically when the condition is met.
    condicion: "mayor_que" | "menor_que" | "fuera_de_rango"
    - "fuera_de_rango" requires valor_min and valor_max.
    - "mayor_que" / "menor_que" require valor_umbral.
    """
    try:
        # Validar condición
        valid_conds = ("mayor_que", "menor_que", "fuera_de_rango")
        if condicion not in valid_conds:
            return f"Condición inválida: '{condicion}'. Opciones: {valid_conds}"

        if condicion == "fuera_de_rango" and (valor_min is None or valor_max is None):
            return "Para 'fuera_de_rango' se requieren valor_min y valor_max."
        if condicion in ("mayor_que", "menor_que") and valor_umbral is None:
            return f"Para '{condicion}' se requiere valor_umbral."

        monitor = Monitor(
            chat_id=chat_id,
            descripcion=descripcion,
            entidad=entidad,
            condicion=condicion,
            valor_min=valor_min,
            valor_max=valor_max,
            valor_umbral=valor_umbral,
            intervalo_minutos=intervalo_minutos,
            activo=True,
        )

        async with async_session() as session:
            session.add(monitor)
            await session.commit()
            await session.refresh(monitor)

        logger.info(f"Monitor creado: id={monitor.id} entidad={entidad} chat={chat_id}")
        return (f"✅ Monitor #{monitor.id} creado:\n"
                f"  Entidad: {entidad}\n"
                f"  Condición: {condicion}\n"
                f"  Intervalo: cada {intervalo_minutos} min\n"
                f"  Descripción: {descripcion}")
    except Exception as e:
        return f"Error creando monitor: {e}"


@tool
async def listar_monitores(chat_id: str) -> str:
    """Lists all active and paused monitors for the user."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Monitor).where(Monitor.chat_id == chat_id)
            )
            monitors = result.scalars().all()

        if not monitors:
            return "No tenés monitores configurados."

        lines = [f"📋 Monitores ({len(monitors)}):"]
        for m in monitors:
            status = "✅ Activo" if m.activo else "⏸️ Pausado"
            lines.append(
                f"  #{m.id} [{status}] {m.entidad} — {m.condicion} "
                f"(umbral: {m.valor_umbral}, rango: {m.valor_min}-{m.valor_max}) "
                f"c/{m.intervalo_minutos}min\n    {m.descripcion or ''}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listando monitores: {e}"


@tool
async def eliminar_monitor(chat_id: str, monitor_id: int) -> str:
    """Permanently deletes a monitor by its ID."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Monitor).where(
                    Monitor.id == monitor_id, Monitor.chat_id == chat_id
                )
            )
            monitor = result.scalar_one_or_none()
            if not monitor:
                return f"Monitor #{monitor_id} no encontrado."
            await session.delete(monitor)
            await session.commit()

        logger.info(f"Monitor eliminado: id={monitor_id} chat={chat_id}")
        return f"🗑️ Monitor #{monitor_id} eliminado."
    except Exception as e:
        return f"Error eliminando monitor: {e}"


@tool
async def pausar_monitor(chat_id: str, monitor_id: int) -> str:
    """Pauses or reactivates a monitor (toggle: active ↔ paused)."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Monitor).where(
                    Monitor.id == monitor_id, Monitor.chat_id == chat_id
                )
            )
            monitor = result.scalar_one_or_none()
            if not monitor:
                return f"Monitor #{monitor_id} no encontrado."

            monitor.activo = not monitor.activo
            await session.commit()
            status = "activado ✅" if monitor.activo else "pausado ⏸️"

        return f"Monitor #{monitor_id} {status}."
    except Exception as e:
        return f"Error pausando monitor: {e}"
