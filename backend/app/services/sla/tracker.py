from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.sla import SLARecord
from app.models.ticket import SupportTicket
from app.services.sla.calendar import calendar_engine


class SLATracker:
    """
    Evaluates SLA progress against business hours calendar and dynamic config thresholds.
    """

    async def update_ticket_sla(
        self,
        db: AsyncSession,
        ticket: SupportTicket,
        current_time: Optional[datetime] = None
    ) -> Optional[SLARecord]:
        now = current_time or Clock.now()

        # Fetch SLA record
        result = await db.execute(
            select(SLARecord).where(SLARecord.ticket_id == ticket.id)
        )
        sla_rec = result.scalar_one_or_none()
        if not sla_rec:
            return None

        # If already completed or resolved, do not modify status further
        if ticket.status == "RESOLVED":
            sla_rec.status = "COMPLETED"
            await db.commit()
            return sla_rec

        # Calculate business minutes elapsed since ticket creation
        start_time = ticket.created_at
        elapsed = calendar_engine.calculate_business_minutes(start_time, now)
        sla_rec.business_minutes_elapsed = elapsed

        target = sla_rec.target_resolution_minutes
        cfg = dynamic_config.get_config()
        warning_pct = cfg.sla_config.get("sla_warning_percentage", 75) / 100.0
        warning_threshold = int(target * warning_pct)

        if elapsed >= target:
            if sla_rec.status != "BREACHED":
                sla_rec.status = "BREACHED"
                sla_rec.breached_at = now

                # Audit Log SLA Breach
                audit = AuditLog(
                    event_type="SLA_BREACH",
                    entity_name="ticket",
                    entity_id=ticket.id,
                    condition_triggered=f"Elapsed business minutes ({elapsed}) exceeded target ({target})",
                    details={
                        "target_resolution_minutes": target,
                        "business_minutes_elapsed": elapsed,
                        "breached_at": now.isoformat()
                    }
                )
                db.add(audit)

        elif elapsed >= warning_threshold:
            if sla_rec.status not in ["WARNING_75", "BREACHED"]:
                sla_rec.status = "WARNING_75"
                sla_rec.warning_75_sent_at = now

                # Audit Log SLA Warning
                audit = AuditLog(
                    event_type="SLA_WARNING",
                    entity_name="ticket",
                    entity_id=ticket.id,
                    condition_triggered=f"Elapsed business minutes ({elapsed}) reached {int(warning_pct*100)}% of target ({target})",
                    details={
                        "target_resolution_minutes": target,
                        "business_minutes_elapsed": elapsed,
                        "warning_sent_at": now.isoformat()
                    }
                )
                db.add(audit)
        else:
            if sla_rec.status not in ["WARNING_75", "BREACHED", "COMPLETED"]:
                sla_rec.status = "OK"

        await db.commit()
        await db.refresh(sla_rec)
        return sla_rec


sla_tracker = SLATracker()
