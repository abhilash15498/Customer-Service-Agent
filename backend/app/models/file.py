import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    file_name = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    storage_path = Column(String(500), nullable=False)
    sha256_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="UPLOADING")  # UPLOADING, PROCESSING, READY, QUARANTINED, PURGED
    rejection_reason = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="files")
    evidence = relationship("ExtractedEvidence", back_populates="file", uselist=False, cascade="all, delete-orphan")


class ExtractedEvidence(Base):
    __tablename__ = "extracted_evidence"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id = Column(String(36), ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False, unique=True)
    extracted_text = Column(Text, nullable=True)
    order_id = Column(String(100), nullable=True)
    amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), nullable=True)
    document_date = Column(Date, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    is_conflicting = Column(Boolean, default=False)
    conflict_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    file = relationship("UploadedFile", back_populates="evidence")
