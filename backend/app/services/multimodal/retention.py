import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.audit import AuditLog
from app.models.file import ExtractedEvidence, UploadedFile


class RetentionManager:
    """
    Manages automated file lifecycle, retention deadlines, and secure file purging (Section 8.8 / Scenario 53).
    """

    @classmethod
    def calculate_expiry(cls, created_at: Optional[datetime] = None) -> datetime:
        base_time = created_at or Clock.now()
        cfg = dynamic_config.get_config()
        retention_days = cfg.file_policy.get("file_retention_days", 7)
        return base_time + timedelta(days=retention_days)

    @classmethod
    async def purge_expired_files(
        cls,
        db: AsyncSession,
        now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        current_time = now or Clock.now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        stmt = select(UploadedFile).where(
            UploadedFile.expires_at <= current_time,
            UploadedFile.status.notin_(["PURGED", "EXPIRED"])
        )
        res = await db.execute(stmt)
        expired_files = res.scalars().all()

        purged_ids = []
        for uf in expired_files:
            # 1. Remove physical file from storage if present
            if uf.storage_path and os.path.exists(uf.storage_path):
                try:
                    os.remove(uf.storage_path)
                except OSError:
                    pass

            # 2. Update status to PURGED
            uf.status = "PURGED"
            purged_ids.append(uf.id)

            # 3. Audit trail
            audit = AuditLog(
                event_type="FILE_PURGED",
                entity_name="uploaded_file",
                entity_id=uf.id,
                condition_triggered="file_retention_expired",
                details={
                    "file_name": uf.file_name,
                    "created_at": uf.created_at.isoformat() if uf.created_at else None,
                    "expired_at": uf.expires_at.isoformat() if uf.expires_at else None,
                    "purged_at": current_time.isoformat()
                }
            )
            db.add(audit)

        await db.commit()
        return {
            "files_scanned": len(expired_files),
            "files_purged": len(purged_ids),
            "purged_file_ids": purged_ids,
            "message": f"Successfully purged {len(purged_ids)} expired files under retention policy."
        }


retention_manager = RetentionManager()
