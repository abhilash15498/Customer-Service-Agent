import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, JSON, String
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(100), nullable=False, index=True)  # ESCALATION, TICKET_CREATED, SLA_WARNING, SLA_BREACH, KB_DEPLOY, KB_ROLLBACK
    entity_name = Column(String(100), nullable=False)  # conversation, ticket, knowledge_version, file
    entity_id = Column(String(36), nullable=False, index=True)
    condition_triggered = Column(String(255), nullable=False)
    details = Column(JSON, default=dict)
    performed_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
