from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.ticket import SupportTicket, TicketAssignment, TicketEvent
from app.models.user import Agent, AgentSkill
from app.services.sla.calendar import calendar_engine


class DispatchRoutingEngine:
    """
    Intelligent dispatch router:
    - Queues: payments, security, legal, on_call, general_support
    - Business hours aware: routes critical issues after-hours to on-call queue
    - Matches skills and balances workload across human support agents
    """

    QUEUE_MAP = {
        "duplicate_payment": "payments",
        "account_compromise": "security",
        "legal_threat": "legal",
        "repeated_frustration": "general_support",
        "15m_unhandled_negative": "general_support",
    }

    SKILL_MAP = {
        "payments": "payments",
        "security": "security",
        "legal": "legal",
        "general_support": "general",
    }

    def determine_queue(self, reason: str, priority: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        current_time = now or Clock.now()
        base_queue = self.QUEUE_MAP.get(reason, "general_support")
        is_business_open = calendar_engine.is_business_hour(current_time)

        cfg = dynamic_config.get_config()
        allow_on_call = cfg.escalation_rules.get("allow_on_call_after_hours", True)

        if not is_business_open:
            if priority.upper() in ["CRITICAL", "HIGH"] and allow_on_call:
                return {
                    "queue": "on_call",
                    "routing_mode": "AFTER_HOURS_ON_CALL",
                    "next_business_time": None,
                    "is_business_hours": False
                }
            else:
                next_open = calendar_engine.get_next_business_time(current_time)
                return {
                    "queue": base_queue,
                    "routing_mode": "SCHEDULED_NEXT_BUSINESS_DAY",
                    "next_business_time": next_open,
                    "is_business_hours": False
                }

        return {
            "queue": base_queue,
            "routing_mode": "IMMEDIATE_BUSINESS_HOURS",
            "next_business_time": None,
            "is_business_hours": True
        }

    async def assign_agent(
        self,
        db: AsyncSession,
        ticket: SupportTicket,
        required_skill: Optional[str] = None
    ) -> Optional[Agent]:
        skill = required_skill or self.SKILL_MAP.get(ticket.required_skill, "general")

        # Find available agents
        stmt = (
            select(Agent)
            .options(selectinload(Agent.skills))
            .where(Agent.is_available == True)
            .where(Agent.current_workload < Agent.max_workload)
        )
        res = await db.execute(stmt)
        candidates = list(res.scalars().all())

        if not candidates:
            return None

        # Filter by skill match if available, otherwise fallback to any available agent
        matching_agents = [
            a for a in candidates
            if any(s.skill_name.lower() == skill.lower() for s in a.skills)
        ]
        pool = matching_agents if matching_agents else candidates

        # Select least loaded agent
        pool.sort(key=lambda a: a.current_workload)
        chosen_agent = pool[0]

        # Create Assignment
        assignment = TicketAssignment(
            ticket_id=ticket.id,
            agent_id=chosen_agent.id,
            assigned_at=Clock.now()
        )
        db.add(assignment)

        # Update ticket status and agent workload
        ticket.status = "ASSIGNED"
        chosen_agent.current_workload += 1

        # Add ticket event
        event = TicketEvent(
            ticket_id=ticket.id,
            event_type="ASSIGNED",
            note=f"Assigned to agent {chosen_agent.id} (skill: {skill})",
            created_at=Clock.now()
        )
        db.add(event)

        await db.commit()
        await db.refresh(ticket)
        return chosen_agent


dispatch_router = DispatchRoutingEngine()
