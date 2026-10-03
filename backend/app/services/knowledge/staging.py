from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.knowledge import KnowledgeChunk, KnowledgeDeployment, KnowledgeDocument, KnowledgeQualityTest, KnowledgeVersion
from app.services.knowledge.parser import DocumentChunk
from app.services.knowledge.quality import quality_evaluator


class StagingDeploymentManager:
    """
    Manages Maintenance Windows, Pre-deployment Quality Gates, and Staged Activations (Sections 6.4, 6.5).
    """

    @classmethod
    def is_in_maintenance_window(cls, check_time: Optional[datetime] = None) -> Tuple[bool, datetime, datetime]:
        now = check_time or Clock.now()
        cfg = dynamic_config.get_config()
        m_win = cfg.knowledge_pipeline.get("maintenance_window", {"start_time": "02:00", "end_time": "03:00"})

        start_h, start_m = map(int, m_win.get("start_time", "02:00").split(":"))
        end_h, end_m = map(int, m_win.get("end_time", "03:00").split(":"))

        current_time_val = now.time()
        start_time_val = time(start_h, start_m)
        end_time_val = time(end_h, end_m)

        today_start = datetime.combine(now.date(), start_time_val, tzinfo=timezone.utc)
        today_end = datetime.combine(now.date(), end_time_val, tzinfo=timezone.utc)

        if start_time_val <= current_time_val < end_time_val:
            return True, today_start, today_end

        # Outside window: determine next window
        if current_time_val < start_time_val:
            next_start = today_start
            next_end = today_end
        else:
            next_start = today_start + timedelta(days=1)
            next_end = today_end + timedelta(days=1)

        return False, next_start, next_end

    @classmethod
    async def stage_or_deploy_version(
        cls,
        db: AsyncSession,
        version_id: str,
        force: bool = False,
        simulated_degradation: bool = False,
        admin_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Attempts to deploy a KnowledgeVersion:
        1. Checks maintenance window.
        2. Executes quality tests.
        3. Activates or schedules.
        """
        now = Clock.now()
        stmt = select(KnowledgeVersion).where(KnowledgeVersion.id == version_id)
        res = await db.execute(stmt)
        version = res.scalar_one_or_none()

        if not version:
            return {"status": "ERROR", "message": f"Version {version_id} not found"}

        in_window, win_start, win_end = cls.is_in_maintenance_window(now)

        # 1. Fetch chunks for quality evaluation
        chunk_stmt = select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
        db_chunks = (await db.execute(chunk_stmt)).scalars().all()
        chunks = [
            DocumentChunk(
                index=c.chunk_index,
                content=c.content,
                section=c.section,
                embedding=c.embedding or [],
                metadata=c.chunk_metadata or {}
            )
            for c in db_chunks
        ]

        # 2. Run quality evaluation test
        doc_stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == version.document_id)
        doc = (await db.execute(doc_stmt)).scalar_one()

        quality_res = await quality_evaluator.evaluate_chunks(
            chunks=chunks,
            title=doc.title,
            simulated_degradation=simulated_degradation
        )

        deployment = KnowledgeDeployment(
            version_id=version.id,
            scheduled_window_start=win_start,
            scheduled_window_end=win_end,
            is_active=False
        )
        db.add(deployment)
        await db.flush()

        # Record Quality Test
        qtest = KnowledgeQualityTest(
            deployment_id=deployment.id,
            grounding_score=quality_res.grounding_score,
            retrieval_mrr=quality_res.retrieval_mrr,
            passed=quality_res.passed,
            details={
                "rejection_reasons": quality_res.rejection_reasons,
                "metrics": quality_res.details
            }
        )
        db.add(qtest)

        if not quality_res.passed:
            version.status = "FAILED"
            deployment.failure_reason = "; ".join(quality_res.rejection_reasons)
            await db.commit()
            return {
                "status": "REJECTED_QUALITY",
                "version_id": version.id,
                "deployment_id": deployment.id,
                "grounding_score": quality_res.grounding_score,
                "retrieval_mrr": quality_res.retrieval_mrr,
                "reasons": quality_res.rejection_reasons
            }

        # 3. Check maintenance window
        if not in_window and not force:
            version.status = "SCHEDULED"
            await db.commit()
            return {
                "status": "SCHEDULED",
                "version_id": version.id,
                "deployment_id": deployment.id,
                "scheduled_start": win_start.isoformat(),
                "scheduled_end": win_end.isoformat(),
                "message": "Deployment scheduled for next maintenance window"
            }

        # 4. In maintenance window or forced: activate!
        # Deactivate older active versions of this document
        old_v_stmt = select(KnowledgeVersion).where(
            KnowledgeVersion.document_id == doc.id,
            KnowledgeVersion.id != version.id,
            KnowledgeVersion.status == "ACTIVE"
        )
        old_versions = (await db.execute(old_v_stmt)).scalars().all()
        for ov in old_versions:
            ov.status = "ARCHIVED"

        version.status = "ACTIVE"
        deployment.deployed_at = now
        deployment.is_active = True

        audit = AuditLog(
            event_type="KB_DEPLOY",
            entity_name="knowledge_version",
            entity_id=version.id,
            condition_triggered="quality_passed_and_window_active",
            details={
                "document_title": doc.title,
                "version": version.version_int,
                "grounding_score": quality_res.grounding_score,
                "force_deployed": force
            },
            performed_by=admin_id
        )
        db.add(audit)
        await db.commit()

        return {
            "status": "ACTIVE",
            "version_id": version.id,
            "deployment_id": deployment.id,
            "deployed_at": now.isoformat(),
            "message": "Knowledge version deployed successfully"
        }


staging_manager = StagingDeploymentManager()
