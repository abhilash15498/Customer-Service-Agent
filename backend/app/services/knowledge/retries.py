from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.knowledge import KnowledgeDeployment, KnowledgeVersion


class IngestionRetryManager:
    """
    Manages Ingestion Failures and Exponential/Scheduled Retries (Section 6.6 / Scenarios 24-27).
    Retry intervals: 15m, 30m, 60m. After 3 retries, marks update as failed.
    """

    @classmethod
    def get_retry_intervals(cls) -> list[int]:
        cfg = dynamic_config.get_config()
        return cfg.knowledge_pipeline.get("retry_intervals_minutes", [15, 30, 60])

    @classmethod
    def calculate_next_retry(
        cls,
        retry_count: int,
        failed_at: Optional[datetime] = None
    ) -> Optional[datetime]:
        intervals = cls.get_retry_intervals()
        if retry_count >= len(intervals):
            return None  # Retries exhausted

        delay_minutes = intervals[retry_count]
        base_time = failed_at or Clock.now()
        return base_time + timedelta(minutes=delay_minutes)

    @classmethod
    async def process_failure(
        cls,
        db: AsyncSession,
        deployment_id: str,
        reason: str,
        now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        current_time = now or Clock.now()
        stmt = select(KnowledgeDeployment).where(KnowledgeDeployment.id == deployment_id)
        res = await db.execute(stmt)
        deployment = res.scalar_one_or_none()

        if not deployment:
            return {"status": "ERROR", "message": f"Deployment {deployment_id} not found"}

        next_retry = cls.calculate_next_retry(deployment.retry_count, current_time)
        deployment.retry_count += 1
        deployment.failure_reason = reason

        v_stmt = select(KnowledgeVersion).where(KnowledgeVersion.id == deployment.version_id)
        version = (await db.execute(v_stmt)).scalar_one()

        if next_retry:
            deployment.scheduled_window_start = next_retry
            version.status = "RETRY_SCHEDULED"
            await db.commit()
            return {
                "status": "RETRY_SCHEDULED",
                "retry_count": deployment.retry_count,
                "next_retry_at": next_retry.isoformat(),
                "reason": reason
            }
        else:
            deployment.is_active = False
            version.status = "FAILED"
            await db.commit()
            return {
                "status": "FAILED",
                "retry_count": deployment.retry_count,
                "reason": f"Max retries exceeded. Last error: {reason}"
            }


retry_manager = IngestionRetryManager()
