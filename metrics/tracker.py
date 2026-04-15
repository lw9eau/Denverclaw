import os
import time
import logging
import asyncio
from datetime import datetime
from db.database import async_session
from metrics.models import RouteEvent, SpecialistEvent, TurnEvent

logger = logging.getLogger("denver.metrics")

class MetricsTracker:
    """
    Clase para el seguimiento de métricas del sistema Denver.
    Implementa un patrón Singleton y asegura que ningún fallo en el tracker
    afecte el flujo principal de la aplicación.
    """

    async def record_route(self, route_type: str, destination: str, latency_ms: float, chat_id: str, interface: str):
        """Registra un evento de ruteo."""
        try:
            async with async_session() as session:
                event = RouteEvent(
                    route_type=route_type,
                    destination=destination,
                    latency_ms=latency_ms,
                    chat_id=chat_id,
                    interface=interface,
                    timestamp=datetime.utcnow()
                )
                session.add(event)
                await session.commit()
        except Exception as e:
            logger.error(f"[MetricsTracker] Error registrando ruta: {e}")

    async def record_specialist(self, name: str, latency_ms: float, chat_id: str, success: bool, error_msg: str = None):
        """Registra la ejecución de un especialista."""
        try:
            async with async_session() as session:
                event = SpecialistEvent(
                    specialist=name,
                    latency_ms=latency_ms,
                    chat_id=chat_id,
                    success=success,
                    error_msg=error_msg,
                    timestamp=datetime.utcnow()
                )
                session.add(event)
                await session.commit()
        except Exception as e:
            logger.error(f"[MetricsTracker] Error registrando especialista: {e}")

    async def record_turn(self, chat_id: str, interface: str, total_latency_ms: float, delegation_count: int, user_text: str = ""):
        """Registra el fin de un turno completo."""
        try:
            # Lógica de guardado de texto
            save_text = os.getenv("METRICS_SAVE_USER_TEXT", "true").lower() != "false"
            
            clean_text = ""
            if save_text and user_text:
                # Truncar a 500
                clean_text = user_text[:500]
                # Ignorar si es data: (binario)
                if clean_text.startswith("data:"):
                    clean_text = ""
            
            async with async_session() as session:
                event = TurnEvent(
                    chat_id=chat_id,
                    interface=interface,
                    total_latency_ms=total_latency_ms,
                    delegation_count=delegation_count,
                    user_text=clean_text,
                    timestamp=datetime.utcnow()
                )
                session.add(event)
                await session.commit()
        except Exception as e:
            logger.error(f"[MetricsTracker] Error registrando turno: {e}")

# Instancia global (Singleton)
tracker = MetricsTracker()
