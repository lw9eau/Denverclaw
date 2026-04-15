from datetime import datetime, timedelta
from sqlalchemy import select, func, desc, case
from db.database import async_session
from metrics.models import RouteEvent, SpecialistEvent, TurnEvent

async def get_route_distribution(hours: int = 24):
    """Retorna la distribución de tipos de ruteo."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(RouteEvent.route_type, func.count(RouteEvent.id))
            .where(RouteEvent.timestamp >= since)
            .group_by(RouteEvent.route_type)
        )
        result = await session.execute(query)
        data = {row[0]: row[1] for row in result.all()}
        
        total = sum(data.values())
        if total == 0:
            return {"direct": 0, "fast": 0, "llm": 0}
            
        return {k: v for k, v in data.items()}

async def get_specialist_distribution(hours: int = 24):
    """Retorna el uso por especialista."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(SpecialistEvent.specialist, func.count(SpecialistEvent.id))
            .where(SpecialistEvent.timestamp >= since)
            .group_by(SpecialistEvent.specialist)
        )
        result = await session.execute(query)
        return {row[0]: row[1] for row in result.all()}

async def get_latency_percentiles(hours: int = 24):
    """Calcula p50, p95 y p99 de latencia de turnos."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(TurnEvent.total_latency_ms)
            .where(TurnEvent.timestamp >= since)
            .order_by(TurnEvent.total_latency_ms)
        )
        result = await session.execute(query)
        latencies = [row[0] for row in result.all()]
        
        if not latencies:
            return {"p50": 0, "p95": 0, "p99": 0}
            
        def percentile(data, p):
            import math
            index = (len(data) - 1) * p
            lower = math.floor(index)
            upper = math.ceil(index)
            if lower == upper:
                return data[lower]
            return data[lower] * (upper - index) + data[upper] * (index - lower)

        return {
            "p50": round(percentile(latencies, 0.5), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2)
        }

async def get_delegation_stats(hours: int = 24):
    """Estadísticas de delegaciones por turno."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = select(
            func.avg(TurnEvent.delegation_count),
            func.max(TurnEvent.delegation_count)
        ).where(TurnEvent.timestamp >= since)
        result = await session.execute(query)
        res = result.one()
        return {
            "avg": round(res[0] or 0, 2),
            "max": res[1] or 0
        }

async def get_error_rate(hours: int = 24):
    """Tasa de error global de especialistas."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = select(
            func.count(SpecialistEvent.id),
            func.sum(case((SpecialistEvent.success == False, 1), else_=0))
        ).where(SpecialistEvent.timestamp >= since)
        result = await session.execute(query)
        res = result.one()
        total = res[0]
        errors = res[1] or 0
        return round(errors / total, 4) if total > 0 else 0

async def get_hourly_volume(hours: int = 24):
    """Volumen de mensajes por hora para gráfico de barras."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        # Nota: SQLite strftime para agrupar por hora usando localtime
        query = (
            select(
                func.strftime('%Y-%m-%d %H:00:00', TurnEvent.timestamp, 'localtime').label('hour'),
                func.count(TurnEvent.id)
            )
            .where(TurnEvent.timestamp >= since)
            .group_by('hour')
            .order_by('hour')
        )
        result = await session.execute(query)
        return [{"hour": row[0], "count": row[1]} for row in result.all()]

async def get_interface_distribution(hours: int = 24):
    """Breakdown por interfaz (telegram, voice, web)."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(TurnEvent.interface, func.count(TurnEvent.id))
            .where(TurnEvent.timestamp >= since)
            .group_by(TurnEvent.interface)
        )
        result = await session.execute(query)
        return {row[0]: row[1] for row in result.all()}

async def get_recent_errors(limit: int = 20, hours: int = 24):
    """Últimos eventos con error."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(SpecialistEvent)
            .where(SpecialistEvent.success == False)
            .where(SpecialistEvent.timestamp >= since)
            .order_by(desc(SpecialistEvent.timestamp))
            .limit(limit)
        )
        result = await session.execute(query)
        return [
            {
                "specialist": e.specialist,
                "timestamp": e.timestamp.isoformat(),
                "error_msg": e.error_msg
            }
            for e in result.scalars().all()
        ]

async def get_summary(hours: int = 24):
    """Consolida todas las métricas para el dashboard."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        total_turns = await session.scalar(
            select(func.count(TurnEvent.id)).where(TurnEvent.timestamp >= since)
        )
    
    latency = await get_latency_percentiles(hours)
    delegation = await get_delegation_stats(hours)
    
    return {
        "route_distribution": await get_route_distribution(hours),
        "top_specialists": await get_specialist_distribution(hours),
        "latency_p50_ms": latency["p50"],
        "latency_p95_ms": latency["p95"],
        "avg_delegations": delegation["avg"],
        "error_rate": await get_error_rate(hours),
        "total_turns_24h": total_turns,
        "interface_breakdown": await get_interface_distribution(hours),
        "hourly_volume": await get_hourly_volume(hours),
        "recent_errors": await get_recent_errors(hours=hours)
    }

async def get_llm_route_by_text(hours: int = 168):
    """
    Identifica mensajes que fueron ruteados por el LLM pero que NO terminaron en FINISH.
    Ayuda a encontrar frases que deberían estar en FastRoute.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(
                func.trim(TurnEvent.user_text),
                RouteEvent.destination,
                func.count(RouteEvent.id).label('frequency'),
                func.avg(RouteEvent.latency_ms).label('avg_latency')
            )
            .join(RouteEvent, (TurnEvent.chat_id == RouteEvent.chat_id) & (TurnEvent.interface == RouteEvent.interface))
            .where(RouteEvent.route_type == 'llm')
            .where(RouteEvent.destination != 'FINISH')
            .where(TurnEvent.user_text != '')
            .where(RouteEvent.timestamp >= since)
            .where(TurnEvent.timestamp >= RouteEvent.timestamp)
            .where(TurnEvent.timestamp <= func.datetime(RouteEvent.timestamp, '+60 seconds'))
            .group_by(func.trim(TurnEvent.user_text), RouteEvent.destination)
            .order_by(desc('frequency'))
            .limit(30)
        )
        result = await session.execute(query)
        return [
            {
                "user_text": row[0],
                "destination": row[1],
                "frequency": row[2],
                "avg_latency": round(row[3] or 0, 2)
            }
            for row in result.all()
        ]

async def get_direct_route_candidates(hours: int = 168):
    """
    Identifica frases que requirieron 2 delegaciones de LLM.
    Ayuda a encontrar patrones para DirectRoute.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    async with async_session() as session:
        query = (
            select(
                func.trim(TurnEvent.user_text),
                SpecialistEvent.specialist,
                func.count(TurnEvent.id).label('frequency'),
                func.avg(TurnEvent.total_latency_ms).label('avg_latency')
            )
            .join(RouteEvent, (TurnEvent.chat_id == RouteEvent.chat_id) & (TurnEvent.interface == RouteEvent.interface))
            .join(SpecialistEvent, (TurnEvent.chat_id == SpecialistEvent.chat_id))
            .where(TurnEvent.delegation_count == 2)
            .where(RouteEvent.route_type == 'llm')
            .where(TurnEvent.user_text != '')
            .where(TurnEvent.timestamp >= since)
            .where(TurnEvent.timestamp >= RouteEvent.timestamp)
            .where(TurnEvent.timestamp <= func.datetime(RouteEvent.timestamp, '+60 seconds'))
            .where(SpecialistEvent.timestamp >= RouteEvent.timestamp)
            .where(SpecialistEvent.timestamp <= func.datetime(RouteEvent.timestamp, '+60 seconds'))
            .group_by(func.trim(TurnEvent.user_text), SpecialistEvent.specialist)
            .having(func.count(TurnEvent.id) >= 2)
            .order_by(desc('frequency'))
            .limit(20)
        )
        result = await session.execute(query)
        return [
            {
                "user_text": row[0],
                "specialist": row[1],
                "frequency": row[2],
                "avg_latency": round(row[3] or 0, 2),
                "savings": round((row[3] or 500) - 500, 2)
            }
            for row in result.all()
        ]
