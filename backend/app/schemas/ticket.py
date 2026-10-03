from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    title: Optional[str] = None
    description: str = Field(..., min_length=1)
    order_id: Optional[str] = None
    product_id: Optional[str] = None
    required_skill: Optional[str] = "general"
    attachments_count: int = 0
    conversation_id: Optional[str] = None


class TicketSLARead(BaseModel):
    ticket_id: str
    target_resolution_minutes: int
    business_minutes_elapsed: int
    status: str
    warning_75_sent_at: Optional[datetime] = None
    breached_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TicketRead(BaseModel):
    id: str
    ticket_number: Optional[int] = None
    customer_id: str
    conversation_id: Optional[str] = None
    title: str
    description: str
    priority: str
    status: str
    required_skill: Optional[str] = "general"
    order_id: Optional[str] = None
    product_id: Optional[str] = None
    parent_ticket_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    sla_record: Optional[TicketSLARead] = None

    class Config:
        from_attributes = True


class TicketCreationResponse(BaseModel):
    ticket: Optional[TicketRead] = None
    is_complete: bool
    missing_fields: List[str] = []
    clarification_prompt: Optional[str] = None
    is_duplicate: bool = False
    parent_ticket_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    routing_status: str = "QUEUED"


class TicketUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    parent_ticket_id: Optional[str] = None
    note: Optional[str] = None


class HandoffSummaryResponse(BaseModel):
    ticket_id: str
    summary: Dict[str, Any]


class EscalationRead(BaseModel):
    id: str
    conversation_id: str
    reason: str
    activated_condition: str
    summary: str
    queue: str
    is_handled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogRead(BaseModel):
    id: str
    event_type: str
    entity_name: str
    entity_id: str
    condition_triggered: str
    details: Optional[Dict[str, Any]] = None
    performed_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
