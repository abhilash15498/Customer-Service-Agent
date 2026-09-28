import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class Escalation(Base):
    __tablename__ = "escalations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(100), nullable=False)  # duplicate_payment, legal_threat, account_compromise, 15m_unresolved
    activated_condition = Column(String(100), nullable=False)  # high_risk_issue, repeated_frustration, sla_timeout
    summary = Column(Text, nullable=False)
    queue = Column(String(100), nullable=False, default="general_support")  # payments, security, legal, on_call
    is_handled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="escalations")
