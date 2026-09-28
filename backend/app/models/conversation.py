import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(50), default="ACTIVE")  # ACTIVE, IDLE, CLOSED, ESCALATED
    last_message_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    conversation_metadata = Column("metadata", JSON, default=dict)

    # Relationships
    customer = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    summaries = relationship("ConversationSummary", back_populates="conversation", cascade="all, delete-orphan")
    escalations = relationship("Escalation", back_populates="conversation")
    tickets = relationship("SupportTicket", back_populates="conversation")
    files = relationship("UploadedFile", back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_role = Column(String(50), nullable=False)  # CUSTOMER, AGENT, ASSISTANT, SYSTEM
    content = Column(Text, nullable=False)
    masked_content = Column(Text, nullable=False)
    language_code = Column(String(10), default="en")
    is_transliterated = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    sentiment = relationship("SentimentAnalysis", back_populates="message", uselist=False, cascade="all, delete-orphan", lazy="selectin")


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    summary_text = Column(Text, nullable=False)
    entities = Column(JSON, default=dict)  # Extracted order_id, product_id, customer contact
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="summaries")
