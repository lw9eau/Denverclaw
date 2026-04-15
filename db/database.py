"""
Denver Bot — Database engine and session factory.
Async SQLite via SQLAlchemy + aiosqlite.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:///./denver.db"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Crea todas las tablas definidas en models.py."""
    from db.models import (  # noqa: F401 — import para registrar modelos en metadata
        Monitor, CalendarNotification, GmailNotification,
        UserConfig, BriefingConfig, Memoria, MemoriaConfig, PendingAction,
    )
    from metrics.models import MetricsBase
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(MetricsBase.metadata.create_all)

        # Migración: Agregar user_text si no existe (SQLite)
        from sqlalchemy import text
        try:
            await conn.execute(text("ALTER TABLE turn_events ADD COLUMN user_text TEXT DEFAULT ''"))
        except Exception:
            # Fallará si la columna ya existe, lo cual es correcto
            pass
