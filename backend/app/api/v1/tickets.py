from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_agent, get_current_customer, get_current_user, get_db
from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.sla import SLARecord
from app.models.ticket import SupportTicket, TicketEvent
from app.models.user import User
from app.schemas.ticket import (
    HandoffSummaryResponse,
    TicketCreate,
    TicketCreationResponse,
    TicketRead,
    TicketSLARead,
    TicketUpdate,
)
from app.services.routing.matcher import skill_workload_matcher
from app.services.sentiment.analyzer import sentiment_analyzer
from app.services.sla.calendar import calendar_engine
from app.services.sla.tracker import sla_tracker
from app.services.tickets.duplicates import duplicate_detector
from app.services.tickets.extractor import ticket_extractor
from app.services.tickets.handoff import handoff_generator
from app.services.tickets.priority import priority_engine

router = APIRouter(prefix="/tickets", tags=["Support Tickets"])


@router.post("", response_model=TicketCreationResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    ticket_in: TicketCreate,
    current_user: User = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a support ticket from customer issue text.
    - Validates mandatory fields (Scenario 10).
    - Detects duplicate & related tickets (Scenarios 11, 12, 13).
    - Calculates deterministic priority and sets SLA.
    - Routes to skilled agent or queues if team is unavailable (Scenarios 14, 15).
    """
    # 1. Extraction & Mandatory Field Validation (Scenario 10)
    extraction = ticket_extractor.extract_and_validate(
        customer_id=current_user.id,
        customer_name=current_user.name,
        customer_email=current_user.email,
        issue_text=ticket_in.description,
        attachments_count=ticket_in.attachments_count
    )

    # Use explicit inputs if provided
    order_id = ticket_in.order_id or extraction.order_id
    product_id = ticket_in.product_id or extraction.product_id

    # If mandatory info is missing and not provided in request -> reject false completeness
    if not extraction.is_complete and not order_id:
        return TicketCreationResponse(
            ticket=None,
            is_complete=False,
            missing_fields=extraction.missing_fields,
            clarification_prompt=extraction.clarification_prompt,
            is_duplicate=False,
            routing_status="PENDING_MANDATORY_INFO"
        )

    # 2. Duplicate Detection (Scenarios 11, 12, 13)
    title = ticket_in.title or extraction.title
    is_dup, parent_ticket, rel_type = await duplicate_detector.find_duplicate_or_related(
        db=db,
        customer_id=current_user.id,
        title=title,
        description=ticket_in.description,
        order_id=order_id
    )

    parent_id = parent_ticket.id if parent_ticket else None

    # 3. Deterministic Priority Calculation (Section 5.2)
    sentiment_res = sentiment_analyzer.analyze_message(ticket_in.description)
    priority = priority_engine.calculate_priority(
        sentiment_res=sentiment_res,
        issue_type=extraction.issue_type
    )

    # 4. Create Ticket Record
    ticket = SupportTicket(
        customer_id=current_user.id,
        conversation_id=ticket_in.conversation_id,
        title=title,
        description=ticket_in.description,
        priority=priority,
        status="OPEN",
        required_skill=ticket_in.required_skill or extraction.issue_type,
        order_id=order_id,
        product_id=product_id,
        parent_ticket_id=parent_id,
        created_at=Clock.now()
    )
    db.add(ticket)
    await db.flush()

    # 5. Initialize SLA Record
    cfg = dynamic_config.get_config()
    resolution_hours = cfg.sla_config.get("default_resolution_hours", 24)
    multiplier = cfg.sla_config.get("priority_multipliers", {}).get(priority, 1.0)
    target_minutes = int(resolution_hours * 60 * multiplier)

    sla_record = SLARecord(
        ticket_id=ticket.id,
        target_resolution_minutes=target_minutes,
        business_minutes_elapsed=0,
        status="OK",
        created_at=Clock.now()
    )
    db.add(sla_record)

    # Add CREATED event
    note_msg = f"Ticket created with priority {priority}. SLA target: {target_minutes} business mins."
    if is_dup:
        note_msg += f" Linked as {rel_type} to parent ticket #{parent_ticket.ticket_number or parent_ticket.id}."
    db.add(TicketEvent(ticket_id=ticket.id, event_type="CREATED", note=note_msg, created_at=Clock.now()))
    await db.commit()

    # 6. Skill-Based Agent Routing (Scenarios 14 & 15)
    assigned, agent, routing_status = await skill_workload_matcher.match_and_assign(
        db=db,
        ticket=ticket,
        required_skill=ticket.required_skill,
        strict_skill=True
    )

    await db.refresh(ticket)
    # Load relationships for response serialization
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == ticket.id)
        .options(selectinload(SupportTicket.sla_record))
    )
    ticket_with_sla = (await db.execute(stmt)).scalar_one()

    return TicketCreationResponse(
        ticket=TicketRead.model_validate(ticket_with_sla),
        is_complete=True,
        missing_fields=[],
        clarification_prompt=None,
        is_duplicate=is_dup,
        parent_ticket_id=parent_id,
        assigned_agent_id=agent.id if agent else None,
        routing_status=routing_status
    )


@router.get("", response_model=List[TicketRead])
async def list_tickets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lists tickets. Customers see only their own tickets; Agents/Admins see all."""
    stmt = select(SupportTicket).options(selectinload(SupportTicket.sla_record))
    if current_user.role == "CUSTOMER":
        stmt = stmt.where(SupportTicket.customer_id == current_user.id)
    stmt = stmt.order_by(SupportTicket.created_at.desc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{id}", response_model=TicketRead)
async def get_ticket(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve ticket details with access verification and SLA record."""
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == id)
        .options(selectinload(SupportTicket.sla_record))
    )
    res = await db.execute(stmt)
    ticket = res.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if current_user.role == "CUSTOMER" and ticket.customer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return ticket


@router.get("/{id}/sla", response_model=TicketSLARead)
async def get_ticket_sla_status(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Calculates and updates real-time SLA consumption against the business calendar (Scenarios 16 & 17)."""
    stmt = select(SupportTicket).where(SupportTicket.id == id)
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    sla_record = await sla_tracker.update_ticket_sla(db, ticket)
    if not sla_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No SLA record found for ticket")

    return TicketSLARead.model_validate(sla_record)


@router.patch("/{id}", response_model=TicketRead)
async def update_ticket(
    id: str,
    ticket_update: TicketUpdate,
    current_agent: User = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db)
):
    """Agent / Admin endpoint to update ticket status, priority, or parent linking."""
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == id)
        .options(selectinload(SupportTicket.sla_record))
    )
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if ticket_update.status:
        ticket.status = ticket_update.status
    if ticket_update.priority:
        ticket.priority = ticket_update.priority
    if ticket_update.parent_ticket_id:
        ticket.parent_ticket_id = ticket_update.parent_ticket_id

    event = TicketEvent(
        ticket_id=ticket.id,
        event_type="UPDATED",
        note=ticket_update.note or f"Ticket updated by agent {current_agent.name}",
        created_at=Clock.now()
    )
    db.add(event)
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get("/{id}/handoff", response_model=HandoffSummaryResponse)
async def get_ticket_handoff_summary(
    id: str,
    current_agent: User = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db)
):
    """Generates a structured, masked handoff summary for human support reps (Section 5.6)."""
    stmt = select(SupportTicket).where(SupportTicket.id == id).options(selectinload(SupportTicket.customer))
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    customer_name = ticket.customer.name if ticket.customer else "Customer"
    summary_data = handoff_generator.generate_summary(
        customer_name=customer_name,
        issue_title=ticket.title,
        order_id=ticket.order_id,
        product_id=ticket.product_id,
        sentiment="frustrated" if ticket.priority in ["HIGH", "CRITICAL"] else "neutral",
        priority=ticket.priority
    )

    return HandoffSummaryResponse(ticket_id=ticket.id, summary=summary_data)
