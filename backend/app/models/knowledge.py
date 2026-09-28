import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)  # SHA-256 for duplicate detection
    product = Column(String(100), nullable=True)
    region = Column(String(50), nullable=True)
    access_level = Column(String(50), nullable=False, default="PUBLIC")  # PUBLIC, INTERNAL, RESTRICTED
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    versions = relationship("KnowledgeVersion", back_populates="document", cascade="all, delete-orphan")


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    version_int = Column(Integer, nullable=False, default=1)
    effective_date = Column(DateTime(timezone=True), nullable=False)
    expiry_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(50), nullable=False, default="DRAFT")  # DRAFT, VALIDATING, APPROVED, SCHEDULED, ACTIVE, FAILED, ROLLED_BACK, QUARANTINED
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    document = relationship("KnowledgeDocument", back_populates="versions")
    chunks = relationship("KnowledgeChunk", back_populates="version", cascade="all, delete-orphan")
    deployments = relationship("KnowledgeDeployment", back_populates="version", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id = Column(String(36), ForeignKey("knowledge_versions.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    section = Column(String(255), nullable=True)
    embedding = Column(JSON, nullable=True)  # Portable embedding vector (list of floats)
    chunk_metadata = Column("metadata", JSON, default=dict)

    version = relationship("KnowledgeVersion", back_populates="chunks")


class KnowledgeDeployment(Base):
    __tablename__ = "knowledge_deployments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id = Column(String(36), ForeignKey("knowledge_versions.id", ondelete="CASCADE"), nullable=False)
    scheduled_window_start = Column(DateTime(timezone=True), nullable=True)
    scheduled_window_end = Column(DateTime(timezone=True), nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    health_checked_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0)
    failure_reason = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False)

    version = relationship("KnowledgeVersion", back_populates="deployments")
    quality_tests = relationship("KnowledgeQualityTest", back_populates="deployment", cascade="all, delete-orphan")


class KnowledgeQualityTest(Base):
    __tablename__ = "knowledge_quality_tests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deployment_id = Column(String(36), ForeignKey("knowledge_deployments.id", ondelete="CASCADE"), nullable=False)
    grounding_score = Column(Float, nullable=False)
    retrieval_mrr = Column(Float, nullable=False)
    passed = Column(Boolean, nullable=False)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    deployment = relationship("KnowledgeDeployment", back_populates="quality_tests")
