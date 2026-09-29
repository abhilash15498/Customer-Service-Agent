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
