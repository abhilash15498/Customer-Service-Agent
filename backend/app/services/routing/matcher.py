from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.models.ticket import SupportTicket, TicketAssignment, TicketEvent
from app.models.user import Agent, AgentSkill


class SkillWorkloadMatcher:
    """
    Skill-based routing and workload balancer (Scenarios 14 & 15).
    - Matches required agent skills (e.g. payments, technical, general).
    - Selects the available agent with the lowest current workload (Scenario 15).
    - If required skill team is unavailable or overloaded, queues ticket in OPEN status (Scenario 14).
    """

    @classmethod
    async def match_and_assign(
        cls,
        db: AsyncSession,
        ticket: SupportTicket,
        required_skill: str = "general",
        strict_skill: bool = True
    ) -> Tuple[bool, Optional[Agent], str]:
        """
        Returns (is_assigned, agent, reason_or_queue_status)
        """
        stmt = (
            select(Agent)
            .options(selectinload(Agent.skills))
            .where(Agent.is_available == True)
            .where(Agent.current_workload < Agent.max_workload)
        )
        res = await db.execute(stmt)
        candidates = list(res.scalars().all())

        if not candidates:
            # Scenario 14: All agents unavailable -> queue ticket, preserve priority
            return False, None, "ALL_AGENTS_UNAVAILABLE_QUEUED"

        # Filter by skill
        matching_agents = [
            a for a in candidates
            if any(s.skill_name.lower() == required_skill.lower() for s in a.skills)
        ]

        if not matching_agents:
            if strict_skill and required_skill.lower() != "general":
                # Scenario 14: Dedicated skill team unavailable -> queue without assigning wrong skill
                return False, None, f"SKILL_TEAM_UNAVAILABLE_{required_skill.upper()}_QUEUED"
            matching_agents = candidates

        # Scenario 15: Select agent with lowest workload
        matching_agents.sort(key=lambda a: a.current_workload)
        best_agent = matching_agents[0]

        # Assign
        assignment = TicketAssignment(
            ticket_id=ticket.id,
            agent_id=best_agent.id,
            assigned_at=Clock.now()
        )
        db.add(assignment)

        ticket.status = "ASSIGNED"
        best_agent.current_workload += 1

        event = TicketEvent(
            ticket_id=ticket.id,
            event_type="ASSIGNED",
            note=f"Assigned to agent {best_agent.id} (skill: {required_skill}, workload: {best_agent.current_workload}/{best_agent.max_workload})",
            created_at=Clock.now()
        )
        db.add(event)
        await db.commit()
        await db.refresh(ticket)
        return True, best_agent, "ASSIGNED_SUCCESS"


skill_workload_matcher = SkillWorkloadMatcher()
