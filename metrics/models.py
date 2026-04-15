from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Index
from sqlalchemy.orm import DeclarativeBase

class MetricsBase(DeclarativeBase):
    pass

class RouteEvent(MetricsBase):
    __tablename__ = "route_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    route_type = Column(String, nullable=False)  # direct | fast | llm
    destination = Column(String, nullable=False) # HomeAutomation, Utility, tool_name, etc.
    latency_ms = Column(Float, nullable=False)
    chat_id = Column(String, nullable=False, index=True)
    interface = Column(String, nullable=False)

class SpecialistEvent(MetricsBase):
    __tablename__ = "specialist_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    specialist = Column(String, nullable=False)  # HomeAutomation | GoogleWorkspace | Utility
    latency_ms = Column(Float, nullable=False)
    chat_id = Column(String, nullable=False, index=True)
    success = Column(Boolean, nullable=False)
    error_msg = Column(Text, nullable=True)

class TurnEvent(MetricsBase):
    __tablename__ = "turn_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    chat_id = Column(String, nullable=False, index=True)
    interface = Column(String, nullable=False)
    total_latency_ms = Column(Float, nullable=False)
    delegation_count = Column(Integer, nullable=False)
    user_text = Column(String(500), nullable=True, default="")
