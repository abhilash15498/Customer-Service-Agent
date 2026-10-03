from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.knowledge import KnowledgeChunk, KnowledgeDeployment, KnowledgeDocument, KnowledgeVersion


class RollbackManager:
    """
    Automated Health Checks and Version Rollback Engine (Section 6.7 / Scenarios 30-31).
    Detects post-deployment failures within grace window and rolls back to known-good versions.
    """

    @classmethod
    async def run_health_check(
        cls,
        db: AsyncSession,
        deployment_id: str,
        simulated_failure: bool = False,
        now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        current_time = now or Clock.now()
        stmt = select(KnowledgeDeployment).where(KnowledgeDeployment.id == deployment_id)
        res = await db.execute(stmt)
        deployment = res.scalar_one_or_none()

        if not deployment:
            return {"status": "ERROR", "message": f"Deployment {deployment_id} not found"}

        # 1. Fetch version
        v_stmt = select(KnowledgeVersion).where(KnowledgeVersion.id == deployment.version_id)
        version = (await db.execute(v_stmt)).scalar_one()

        # 2. Check health grace window
        cfg = dynamic_config.get_config()
        grace_mins = cfg.knowledge_pipeline.get("health_check_grace_minutes", 5)

        if deployment.deployed_at:
            dep_time = deployment.deployed_at
            if dep_time.tzinfo is None:
                dep_time = dep_time.replace(tzinfo=timezone.utc)
            curr_norm = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
            elapsed_minutes = (curr_norm - dep_time).total_seconds() / 60.0
            within_grace = elapsed_minutes <= grace_mins
        else:
            within_grace = True

        # 3. Verify chunk integrity
        chunk_stmt = select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
        chunks = (await db.execute(chunk_stmt)).scalars().all()
        has_chunks = len(chunks) > 0 and all(c.embedding and len(c.embedding) > 0 for c in chunks)

        is_healthy = has_chunks and not simulated_failure

        deployment.health_checked_at = current_time

        if is_healthy:
            await db.commit()
            return {
                "status": "HEALTHY",
                "deployment_id": deployment.id,
                "version_id": version.id,
                "checked_at": current_time.isoformat(),
                "within_grace_window": within_grace
            }

        # Failed Health Check!
        failure_reason = "Simulated health check failure" if simulated_failure else "Empty or corrupted chunk embeddings"
        rollback_res = await cls.execute_rollback(
            db=db,
            deployment_id=deployment.id,
            reason=failure_reason,
            is_automatic=True
        )
        return {
            "status": "HEALTH_CHECK_FAILED",
            "deployment_id": deployment.id,
            "failure_reason": failure_reason,
            "rollback": rollback_res
        }

    @classmethod
    async def execute_rollback(
        cls,
        db: AsyncSession,
        deployment_id: str,
        reason: str,
        is_automatic: bool = True,
        performed_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Restores previous known-good version of the document and creates an audit record.
        """
        now = Clock.now()
        stmt = select(KnowledgeDeployment).where(KnowledgeDeployment.id == deployment_id)
        res = await db.execute(stmt)
        deployment = res.scalar_one_or_none()

        if not deployment:
            return {"status": "ERROR", "message": f"Deployment {deployment_id} not found"}

        v_stmt = select(KnowledgeVersion).where(KnowledgeVersion.id == deployment.version_id)
        failed_version = (await db.execute(v_stmt)).scalar_one()

        # Find previous version
        prev_stmt = (
            select(KnowledgeVersion)
            .where(
                KnowledgeVersion.document_id == failed_version.document_id,
                KnowledgeVersion.version_int < failed_version.version_int,
                KnowledgeVersion.status.in_(["ACTIVE", "ARCHIVED", "APPROVED"])
            )
            .order_by(KnowledgeVersion.version_int.desc())
        )
        prev_res = await db.execute(prev_stmt)
        previous_version = prev_res.scalars().first()

        # Update failed version & deployment
        failed_version.status = "ROLLED_BACK"
        deployment.is_active = False
        deployment.failure_reason = reason

        restored_id = None
        restored_ver_int = None
        if previous_version:
            previous_version.status = "ACTIVE"
            restored_id = previous_version.id
            restored_ver_int = previous_version.version_int

        # Record auditable log
        doc_stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == failed_version.document_id)
        doc = (await db.execute(doc_stmt)).scalar_one()

        audit = AuditLog(
            event_type="KB_ROLLBACK",
            entity_name="knowledge_version",
            entity_id=failed_version.id,
            condition_triggered="health_check_failure" if is_automatic else "manual_admin_rollback",
            details={
                "document_title": doc.title,
                "rolled_back_version": failed_version.version_int,
                "restored_version": restored_ver_int,
                "restored_version_id": restored_id,
                "reason": reason,
                "automatic": is_automatic
            },
            performed_by=performed_by
        )
        db.add(audit)
        await db.commit()

        return {
            "status": "ROLLED_BACK",
            "rolled_back_version_id": failed_version.id,
            "rolled_back_version_int": failed_version.version_int,
            "restored_version_id": restored_id,
            "restored_version_int": restored_ver_int,
            "reason": reason,
            "automatic": is_automatic
        }


rollback_manager = RollbackManager()
