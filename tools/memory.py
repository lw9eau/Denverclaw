"""
Denver Bot — Async tools for persistent key/value memory.
Two-level control: global (MEMORIA_PERSISTENTE env) and per-user (MemoriaConfig).
"""

import os
import logging
from datetime import datetime
from langchain_core.tools import tool
from sqlalchemy import select, delete
from db.database import async_session
from db.models import Memoria, MemoriaConfig

logger = logging.getLogger("denver.tools.memory")


def _is_memory_globally_enabled() -> bool:
    """Check global MEMORIA_PERSISTENTE flag from .env."""
    return os.getenv("MEMORIA_PERSISTENTE", "true").lower() == "true"


async def _is_memory_enabled_for_user(chat_id: str) -> bool:
    """Check per-user memory flag from MemoriaConfig. Default: True if no row exists."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoriaConfig).where(MemoriaConfig.chat_id == chat_id)
        )
        config = result.scalar_one_or_none()
        return config.activa if config else True


async def _check_memory_access(chat_id: str) -> str | None:
    """
    Verifica ambos niveles de control.
    Retorna None si la memoria está habilitada, o un mensaje de error si no.
    """
    if not _is_memory_globally_enabled():
        return "La memoria persistente está desactivada globalmente (MEMORIA_PERSISTENTE=false)."
    if not await _is_memory_enabled_for_user(chat_id):
        return "La memoria está desactivada para tu usuario. Usá 'activar memoria' para reactivarla."
    return None


@tool
async def guardar_memoria(chat_id: str, clave: str, valor: str,
                           descripcion: str = "") -> str:
    """
    Saves or updates a piece of data in the user's persistent memory (upsert by key).
    Checks both control levels before writing.
    clave: snake_case, short and descriptive.
    Examples:
    - clave="principal_player", valor="media_player.mezzanine_player"
    - clave="front_camera", valor="camera.front"
    - clave="dog_name", valor="Mateo"
    """
    try:
        error = await _check_memory_access(chat_id)
        if error:
            return f"⚠️ {error} No se guardó el dato."

        async with async_session() as session:
            result = await session.execute(
                select(Memoria).where(
                    Memoria.chat_id == chat_id, Memoria.clave == clave
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.valor = valor
                existing.descripcion = descripcion or existing.descripcion
                existing.updated_at = datetime.utcnow()
                action = "actualizada"
            else:
                memoria = Memoria(
                    chat_id=chat_id,
                    clave=clave,
                    valor=valor,
                    descripcion=descripcion,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(memoria)
                action = "guardada"

            await session.commit()

        logger.info(f"[Memory] guardar clave=\"{clave}\" chat={chat_id}")
        return f"🧠 Memoria {action}: {clave} = {valor}"
    except Exception as e:
        return f"Error guardando memoria: {e}"


@tool
async def guardar_regla(chat_id: str, nombre_regla: str, instruccion: str,
                        descripcion: str = "") -> str:
    """
    Saves a behavioral rule for Denver. These rules affect how Denver processes requests.
    nombre_regla: short snake_case name (e.g., 'clima_ropa', 'formato_noticias').
    instruccion: the specific instruction Denver should follow (e.g., 'siempre sugerí ropa', 'solo titulares').
    """
    # Prepend 'regla_' to ensure it's identified as a rule in the supervisor/specialists
    clave_regla = f"regla_{nombre_regla}" if not nombre_regla.startswith("regla_") else nombre_regla
    return await guardar_memoria.ainvoke({
        "chat_id": chat_id,
        "clave": clave_regla,
        "valor": instruccion,
        "descripcion": descripcion or "Regla de comportamiento dinámica."
    })


@tool
async def eliminar_regla(chat_id: str, nombre_regla: str) -> str:
    """
    Deletes a behavioral rule for Denver.
    nombre_regla: the snake_case name of the rule to delete.
    """
    clave_regla = f"regla_{nombre_regla}" if not nombre_regla.startswith("regla_") else nombre_regla
    return await eliminar_memoria.ainvoke({
        "chat_id": chat_id,
        "clave": clave_regla
    })


@tool
async def consultar_memoria(chat_id: str, clave: str) -> str:
    """Queries a specific memory value by its key."""
    try:
        if not _is_memory_globally_enabled():
            return "La memoria persistente está desactivada globalmente."

        async with async_session() as session:
            result = await session.execute(
                select(Memoria).where(
                    Memoria.chat_id == chat_id, Memoria.clave == clave
                )
            )
            memoria = result.scalar_one_or_none()

        if memoria:
            logger.info(f"[Memory] consultar clave=\"{clave}\" chat={chat_id} → encontrado")
            desc = f" ({memoria.descripcion})" if memoria.descripcion else ""
            return f"🧠 {clave}: {memoria.valor}{desc}"
        else:
            logger.info(f"[Memory] consultar clave=\"{clave}\" chat={chat_id} → no encontrado")
            return f"No tengo guardada la clave '{clave}'."
    except Exception as e:
        return f"Error consultando memoria: {e}"


@tool
async def listar_memorias(chat_id: str) -> str:
    """Lists all user memories with key, value, and description."""
    try:
        if not _is_memory_globally_enabled():
            return "La memoria persistente está desactivada globalmente."

        async with async_session() as session:
            result = await session.execute(
                select(Memoria).where(Memoria.chat_id == chat_id)
            )
            memorias = result.scalars().all()

        if not memorias:
            return "No tenés memorias guardadas."

        lines = [f"🧠 Memorias ({len(memorias)}):"]
        for m in memorias:
            desc = f" — {m.descripcion}" if m.descripcion else ""
            lines.append(f"  • {m.clave}: {m.valor}{desc}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error listando memorias: {e}"


@tool
async def eliminar_memoria(chat_id: str, clave: str) -> str:
    """Deletes a specific memory by its key."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Memoria).where(
                    Memoria.chat_id == chat_id, Memoria.clave == clave
                )
            )
            memoria = result.scalar_one_or_none()
            if not memoria:
                return f"No se encontró la clave '{clave}'."
            await session.delete(memoria)
            await session.commit()

        logger.info(f"[Memory] eliminar clave=\"{clave}\" chat={chat_id}")
        return f"🗑️ Memoria eliminada: {clave}"
    except Exception as e:
        return f"Error eliminando memoria: {e}"


@tool
async def eliminar_todas_las_memorias(chat_id: str) -> str:
    """
    Deletes ALL stored memories for the current user.
    Use when the user explicitly asks to "clear all memory", "forget everything", etc.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Memoria).where(Memoria.chat_id == chat_id)
            )
            memorias = result.scalars().all()
            
            if not memorias:
                return "No tenías ninguna memoria guardada."
                
            count = len(memorias)
            for m in memorias:
                await session.delete(m)
            await session.commit()

        logger.info(f"[Memory] eliminar_todas_las_memorias chat={chat_id} (borradas {count})")
        return f"🗑️ Se han eliminado con éxito todas tus memorias ({count} en total)."
    except Exception as e:
        return f"Error eliminando todas las memorias: {e}"


@tool
async def configurar_memoria(chat_id: str, activa: bool) -> str:
    """
    Activates or deactivates persistent memory for this user.
    When deactivated: no context is injected and no new memories are saved.
    Existing data is preserved in the DB and recovered upon reactivation.
    """
    try:
        if not _is_memory_globally_enabled():
            return ("La memoria persistente está desactivada globalmente "
                    "(MEMORIA_PERSISTENTE=false). No se puede cambiar por usuario.")

        async with async_session() as session:
            result = await session.execute(
                select(MemoriaConfig).where(MemoriaConfig.chat_id == chat_id)
            )
            config = result.scalar_one_or_none()

            if config:
                config.activa = activa
            else:
                config = MemoriaConfig(chat_id=chat_id, activa=activa)
                session.add(config)

            await session.commit()

        status = "activada ✅" if activa else "desactivada ❌"
        extra = "" if activa else " Tus datos guardados se conservan."
        return f"🧠 Memoria {status}.{extra}"
    except Exception as e:
        return f"Error configurando memoria: {e}"
