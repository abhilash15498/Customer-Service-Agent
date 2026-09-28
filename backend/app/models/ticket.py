import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_number = Column(Integer, autoincrement=True, unique=True, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    customer_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(String(50), nullable=False, default="MEDIUM")  # LOW, MEDIUM, HIGH, CRITICAL
    status = Column(String(50), nullable=False, default="OPEN")  # OPEN, PENDING_INFO, ASSIGNED, IN_PROGRESS, RESOLVED
    required_skill = Column(String(100), default="general")
    order_id = Column(String(100), nullable=True)
    product_id = Column(String(100), nullable=True)
    parent_ticket_id = Column(String(36), ForeignKey("support_tickets.id"), nullable=True)  # For duplicate/grouped tickets
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    customer = relationship("User", back_populates="tickets")
    conversation = relationship("Conversation", back_populates="tickets")
    assignments = relationship("TicketAssignment", back_populates="ticket", cascade="all, delete-orphan")
    sla_record = relationship("SLARecord", back_populates="ticket", uselist=False, cascade="all, delete-orphan")
    events = relationship("TicketEvent", back_populates="ticket", cascade="all, delete-orphan")


class TicketAssignment(Base):
    __tablename__ = "ticket_assignments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id = Column(String(36), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_at = Column(DateTime(timezone=True), nullable=True)

    ticket = relationship("SupportTicket", back_populates="assignments")
    agent = relationship("Agent", back_populates="assignments")


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id = Column(String(36), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(100), nullable=False)  # CREATED, ASSIGNED, PRIORITY_CHANGED, STATUS_CHANGED
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ticket = relationship("SupportTicket", back_populates="events")
