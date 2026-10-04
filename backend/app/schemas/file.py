from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    id: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    sha256_hash: str
    status: str  # UPLOADING, PROCESSING, READY, QUARANTINED, PURGED, EXPIRED
    expires_at: datetime
    created_at: datetime
    processing_mode: str = "SYNC"  # SYNC or ASYNC
    message: Optional[str] = None

    class Config:
        from_attributes = True


class EvidenceRead(BaseModel):
    id: str
    file_id: str
    extracted_text: Optional[str] = None
    order_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    document_date: Optional[date] = None
    confidence: float
    is_conflicting: bool
    conflict_notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class FileDetailRead(BaseModel):
    id: str
    conversation_id: Optional[str] = None
    file_name: str
    mime_type: str
    file_size_bytes: int
    status: str
    rejection_reason: Optional[str] = None
    expires_at: datetime
    created_at: datetime
    evidence: Optional[EvidenceRead] = None

    class Config:
        from_attributes = True


class MultimodalAnalysisRequest(BaseModel):
    claimed_order_id: Optional[str] = None
    claimed_amount: Optional[float] = None
    customer_message: Optional[str] = None
    simulated_ocr_text: Optional[str] = None
    simulated_quality: Optional[str] = None  # "CLEAR", "BLURRED", "OCR_FAILURE", "TIMEOUT"
    simulated_processing_seconds: Optional[int] = None


class MultimodalAnalysisResponse(BaseModel):
    file_id: str
    status: str  # READY, LOW_QUALITY, CONFLICT, MATCH, MISSING_INFO, QUARANTINED, PROCESSING_ASYNC
    confidence: float
    extracted_order_id: Optional[str] = None
    extracted_amount: Optional[float] = None
    extracted_currency: Optional[str] = None
    extracted_date: Optional[str] = None
    comparison_result: str  # MATCH, CONFLICT, MISSING_INFO, LOW_QUALITY, UNCHECKED
    is_conflicting: bool = False
    customer_prompt: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class RetentionCleanupResponse(BaseModel):
    files_scanned: int
    files_purged: int
    purged_file_ids: List[str] = []
    message: str
