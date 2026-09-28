import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, JSON, String
from sqlalchemy.orm import relationship
from app.core.database import Base


class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analysis"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = Column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    sentiment = Column(String(50), nullable=False)  # positive, neutral, negative, frustrated, urgent, sarcastic
    confidence = Column(Float, nullable=False)
    urgency = Column(String(20), nullable=False, default="low")  # low, medium, high, critical
    sarcasm = Column(Boolean, default=False)
    risk_type = Column(String(100), nullable=True)  # account_compromise, duplicate_payment, legal_threat
    raw_scores = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    message = relationship("Message", back_populates="sentiment")
