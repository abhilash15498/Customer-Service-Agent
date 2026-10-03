from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    product: Optional[str] = None
    region: Optional[str] = None
    access_level: str = "PUBLIC"  # PUBLIC, INTERNAL, RESTRICTED
    version_int: int = 1
    effective_date: datetime
    expiry_date: datetime
    status: str = "ACTIVE"  # DRAFT, ACTIVE, etc.


class DocumentVersionRead(BaseModel):
    id: str
    document_id: str
    document_title: str
    version_int: int
    effective_date: datetime
    expiry_date: datetime
    status: str
    access_level: str
    created_at: datetime

    class Config:
        from_attributes = True


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 3
    product: Optional[str] = None
    region: Optional[str] = None


class CitationSchema(BaseModel):
    document: str
    version: int
    section: Optional[str] = None


class KnowledgeQueryResponse(BaseModel):
    query: str
    answer: str
    citations: List[CitationSchema] = Field(default_factory=list)
    grounded: bool
    refused: bool = False
    unsupported_claim_detected: bool = False
    warning_notes: Optional[str] = None


class DeployRequest(BaseModel):
    force: bool = False
    simulated_degradation: bool = False


class DeployResponse(BaseModel):
    status: str
    version_id: str
    deployment_id: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    deployed_at: Optional[str] = None
    grounding_score: Optional[float] = None
    retrieval_mrr: Optional[float] = None
    reasons: Optional[List[str]] = None
    message: Optional[str] = None


class HealthCheckRequest(BaseModel):
    simulated_failure: bool = False


class HealthCheckResponse(BaseModel):
    status: str
    deployment_id: str
    failure_reason: Optional[str] = None
    rollback: Optional[Dict[str, Any]] = None
    checked_at: Optional[str] = None
    within_grace_window: Optional[bool] = None


class RollbackRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RollbackResponse(BaseModel):
    status: str
    rolled_back_version_id: str
    rolled_back_version_int: int
    restored_version_id: Optional[str] = None
    restored_version_int: Optional[int] = None
    reason: str
    automatic: bool


class RetryRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RetryResponse(BaseModel):
    status: str
    retry_count: int
    next_retry_at: Optional[str] = None
    reason: Optional[str] = None
