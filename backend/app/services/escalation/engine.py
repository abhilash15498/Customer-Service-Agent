from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.conversation import Conversation, Message
from app.models.escalation import Escalation
from app.models.sentiment import SentimentAnalysis
from app.models.sla import SLARecord
from app.models.ticket import SupportTicket, TicketEvent
from app.services.routing.dispatcher import dispatch_router
from app.services.sentiment.analyzer import SentimentResult
from app.services.sla.calendar import calendar_engine


class EscalationDecision(BaseModel):
    should_escalate: bool = False
    reason: Optional[str] = None
    activated_condition: Optional[str] = None
    priority: str = "MEDIUM"


class EscalationResult(BaseModel):
    escalated: bool
    reason: Optional[str] = None
    activated_condition: Optional[str] = None
    queue: Optional[str] = None
    ticket_id: Optional[str] = None
    priority: Optional[str] = None


class EscalationEngine:
    """
    Deterministic Escalation Engine:
    - High-risk auto-escalation (account_compromise, duplicate_payment, legal_threat)
    - 3-streak consecutive negative messages
    - 15-minute unhandled negative conversation timer
    - Full audit logging and automated SupportTicket generation
    """

    def evaluate_triggers(
        self,
        sentiment_res: SentimentResult,
        user_message: str
    ) -> EscalationDecision:
        cfg = dynamic_config.get_config()
        high_risk_cats = cfg.escalation_rules.get("high_risk_categories", [
            "account_compromise",
            "duplicate_payment",
            "legal_threat"
        ])
        negative_streak_threshold = int(cfg.sentiment_thresholds.get("negative_streak_escalation_count", 3))

        # 1. High Risk Check (Immediate Priority)
        if sentiment_res.risk_type and sentiment_res.risk_type in high_risk_cats:
            risk = sentiment_res.risk_type
            priority = "CRITICAL" if risk in ["account_compromise", "duplicate_payment"] else "HIGH"
            return EscalationDecision(
                should_escalate=True,
                reason=risk,
                activated_condition="high_risk_issue",
                priority=priority
            )

        # 2. Repeated Frustration / Negative Streak Check
        if sentiment_res.negative_streak_count >= negative_streak_threshold:
            return EscalationDecision(
                should_escalate=True,
                reason="repeated_frustration",
                activated_condition="repeated_frustration",
                priority="HIGH"
            )

        return EscalationDecision(should_escalate=False)

    async def execute_escalation(
        self,
        db: AsyncSession,
        conversation: Conversation,
        decision: EscalationDecision,
        summary_text: str
    ) -> EscalationResult:
        now = Clock.now()

        # Check if conversation already has an active, unhandled escalation
        existing_esc_res = await db.execute(
            select(Escalation)
            .where(Escalation.conversation_id == conversation.id)
            .where(Escalation.is_handled == False)
        )
        existing_esc = existing_esc_res.scalars().first()

        # Determine queue & after-hours routing
        route_info = dispatch_router.determine_queue(
            reason=decision.reason or "general_support",
            priority=decision.priority,
            now=now
        )
        queue = route_info["queue"]

        if existing_esc:
            # Ticket might already exist for conversation
            ticket_res = await db.execute(
                select(SupportTicket).where(SupportTicket.conversation_id == conversation.id)
            )
            ticket = ticket_res.scalars().first()
            return EscalationResult(
                escalated=True,
                reason=existing_esc.reason,
                activated_condition=existing_esc.activated_condition,
                queue=existing_esc.queue,
                ticket_id=ticket.id if ticket else None,
                priority=decision.priority
            )

        # 1. Create Escalation Record
        escalation = Escalation(
            conversation_id=conversation.id,
            reason=decision.reason or "general_support",
            activated_condition=decision.activated_condition or "manual",
            summary=summary_text[:500],
            queue=queue,
            is_handled=False,
            created_at=now
        )
        db.add(escalation)

        # 2. Create Support Ticket with SLA targets
        ticket = SupportTicket(
            conversation_id=conversation.id,
            customer_id=conversation.customer_id,
            title=f"[{decision.reason or 'Escalation'}] Support Request",
            description=summary_text,
            priority=decision.priority,
            status="OPEN",
            required_skill=queue if queue != "on_call" else "general",
            created_at=now,
            updated_at=now
        )
        db.add(ticket)
        await db.flush()

        # 3. Create SLA Record
        sla_targets = calendar_engine.calculate_sla_targets(decision.priority, start_dt=now)
        sla_record = SLARecord(
            ticket_id=ticket.id,
            target_response_minutes=sla_targets["target_response_minutes"],
            target_resolution_minutes=sla_targets["target_resolution_minutes"],
            business_minutes_elapsed=0,
            status="OK",
            created_at=now
        )
        db.add(sla_record)

        # 4. Ticket Lifecycle Events
        event_created = TicketEvent(
            ticket_id=ticket.id,
            event_type="CREATED",
            note=f"Ticket created automatically via escalation engine ({decision.reason})",
            created_at=now
        )
        event_escalated = TicketEvent(
            ticket_id=ticket.id,
            event_type="ESCALATED",
            note=f"Escalated condition: {decision.activated_condition} -> Queue: {queue}",
            created_at=now
        )
        db.add(event_created)
        db.add(event_escalated)

        # 5. Audit Log Entry
        audit = AuditLog(
            event_type="ESCALATION",
            entity_name="conversation",
            entity_id=conversation.id,
            condition_triggered=f"{decision.activated_condition}: {decision.reason}",
            details={
                "reason": decision.reason,
                "priority": decision.priority,
                "queue": queue,
                "ticket_id": ticket.id,
                "routing_mode": route_info["routing_mode"]
            },
            performed_by=conversation.customer_id,
            created_at=now
        )
        db.add(audit)

        # 6. Try assigning human agent if during business hours
        if route_info["is_business_hours"]:
            await dispatch_router.assign_agent(db, ticket, required_skill=ticket.required_skill)

        await db.commit()
        await db.refresh(ticket)
        await db.refresh(escalation)

        return EscalationResult(
            escalated=True,
            reason=escalation.reason,
            activated_condition=escalation.activated_condition,
            queue=queue,
            ticket_id=ticket.id,
            priority=decision.priority
        )

    async def check_unhandled_negative_conversations(self, db: AsyncSession) -> List[EscalationResult]:
        """
        Scenario: 15-minute unhandled negative conversation timer.
        Inspects active conversations where the customer's last message has negative/frustrated sentiment
        and has remained unhandled for >= negative_escalation_minutes.
        """
        now = Clock.now()
        cfg = dynamic_config.get_config()
        unhandled_minutes = cfg.escalation_rules.get("negative_escalation_minutes", 15)
        cutoff_time = now - timedelta(minutes=unhandled_minutes)

        # Find active conversations updated before cutoff_time
        stmt = (
            select(Conversation)
            .where(Conversation.status == "ACTIVE")
            .where(Conversation.last_message_at <= cutoff_time)
            .options(selectinload(Conversation.messages))
        )
        res = await db.execute(stmt)
        conversations = res.scalars().all()

        results = []
        for conv in conversations:
            # Check if already escalated and unhandled
            existing_esc_res = await db.execute(
                select(Escalation)
                .where(Escalation.conversation_id == conv.id)
                .where(Escalation.is_handled == False)
            )
            if existing_esc_res.scalars().first():
                continue

            # Check last customer message
            customer_msgs = [m for m in conv.messages if m.sender_role == "CUSTOMER"]
            if not customer_msgs:
                continue

            last_msg = customer_msgs[-1]
            sent_res = await db.execute(
                select(SentimentAnalysis).where(SentimentAnalysis.message_id == last_msg.id)
            )
            sentiment_row = sent_res.scalars().first()

            if sentiment_row and sentiment_row.sentiment in ["negative", "frustrated", "sarcastic"]:
                decision = EscalationDecision(
                    should_escalate=True,
                    reason="15m_unhandled_negative",
                    activated_condition="sla_timeout",
                    priority="HIGH"
                )
                esc_result = await self.execute_escalation(
                    db=db,
                    conversation=conv,
                    decision=decision,
                    summary_text=f"Customer issue unhandled for >= {unhandled_minutes} minutes: {last_msg.content}"
                )
                results.append(esc_result)

        return results


escalation_engine = EscalationEngine()
