import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base


class SLARecord(Base):
    __tablename__ = "sla_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id = Column(String(36), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, unique=True)
    target_response_minutes = Column(Integer, nullable=False, default=120)
    target_resolution_minutes = Column(Integer, nullable=False, default=1440)
    business_minutes_elapsed = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="OK")  # OK, WARNING_75, BREACHED, COMPLETED
    warning_75_sent_at = Column(DateTime(timezone=True), nullable=True)
    breached_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ticket = relationship("SupportTicket", back_populates="sla_record")
