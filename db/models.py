"""
Denver Bot — SQLAlchemy ORM models (7 tables).
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON,
    UniqueConstraint,
)
from db.database import Base


class Monitor(Base):
    """Reglas de monitoreo proactivo de entidades HA."""
    __tablename__ = "monitors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    descripcion = Column(Text, nullable=True)
    entidad = Column(String, nullable=False)
    condicion = Column(String, nullable=False)  # mayor_que | menor_que | fuera_de_rango
    valor_min = Column(Float, nullable=True)
    valor_max = Column(Float, nullable=True)
    valor_umbral = Column(Float, nullable=True)
    intervalo_minutos = Column(Integer, default=10)
    ultima_notificacion = Column(DateTime, nullable=True)
    activo = Column(Boolean, default=True)




class CalendarNotification(Base):
    """Deduplicación de recordatorios de calendario."""
    __tablename__ = "calendar_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    notificado_en = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "event_id"),)


class GmailNotification(Base):
    """Deduplicación de notificaciones de mail urgente."""
    __tablename__ = "gmail_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    message_id = Column(String, nullable=False)
    notificado_en = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)


class UserConfig(Base):
    """Configuración de notificaciones proactivas por usuario."""
    __tablename__ = "user_configs"

    chat_id = Column(String, primary_key=True)
    calendar_activo = Column(Boolean, default=True)
    calendar_anticipacion_minutos = Column(Integer, default=60)
    gmail_activo = Column(Boolean, default=True)
    gmail_pausa_hasta = Column(DateTime, nullable=True)


class BriefingConfig(Base):
    """Configuración del resumen matutino por usuario."""
    __tablename__ = "briefing_configs"

    chat_id = Column(String, primary_key=True)
    activo = Column(Boolean, default=True)
    hora = Column(Integer, default=7)
    incluir_clima = Column(Boolean, default=True)
    incluir_calendario = Column(Boolean, default=True)
    incluir_gmail = Column(Boolean, default=True)
    incluir_hogar = Column(Boolean, default=True)
    ultimo_envio = Column(DateTime, nullable=True)


class Memoria(Base):
    """Memoria persistente clave/valor por usuario."""
    __tablename__ = "memorias"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    clave = Column(String, nullable=False)
    valor = Column(Text, nullable=False)
    descripcion = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("chat_id", "clave"),)


class MemoriaConfig(Base):
    """Control on/off de memoria persistente por usuario."""
    __tablename__ = "memoria_configs"

    chat_id = Column(String, primary_key=True)
    activa = Column(Boolean, default=True)


class PendingAction(Base):
    """Reservado para botones inline (no implementar aún)."""
    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    action_type = Column(String, nullable=False)
    action_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
