from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    client_timestamp: Optional[datetime] = None


class CitationRead(BaseModel):
    document: str
    version: int
    section: Optional[str] = None
    source_url: Optional[str] = None


class SentimentRead(BaseModel):
    sentiment: str
    confidence: float
    urgency: str
    sarcasm: bool = False
    risk_type: Optional[str] = None


class MessageRead(BaseModel):
    id: str
    conversation_id: str
    sender_role: str
    content: str
    masked_content: str
    language_code: Optional[str] = "en"
    is_transliterated: bool = False
    sentiment: Optional[SentimentRead] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    user_message: MessageRead
    assistant_message: MessageRead
    escalated: bool = False
    escalation_reason: Optional[str] = None
    ticket_id: Optional[str] = None
    citations: List[CitationRead] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    metadata: Optional[Dict[str, Any]] = None


class ConversationRead(BaseModel):
    id: str
    customer_id: str
    status: str
    last_message_at: datetime
    created_at: datetime
    message_count: Optional[int] = 0

    class Config:
        from_attributes = True


class ConversationSummaryRead(BaseModel):
    conversation_id: str
    summary_text: str
    entities: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True
