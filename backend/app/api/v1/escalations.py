from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.deps import get_current_admin, get_current_user, get_db
from app.models.audit import AuditLog
from app.models.escalation import Escalation
from app.models.user import User
from app.schemas.ticket import AuditLogRead, EscalationRead
from app.services.escalation.engine import escalation_engine

router = APIRouter(prefix="/escalations", tags=["Escalations & Audit"])


@router.get("", response_model=List[EscalationRead])
async def list_escalations(
    queue: Optional[str] = Query(None),
    is_handled: Optional[bool] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == "CUSTOMER":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access only")

    stmt = select(Escalation).order_by(Escalation.created_at.desc())
    if queue:
        stmt = stmt.where(Escalation.queue == queue)
    if is_handled is not None:
        stmt = stmt.where(Escalation.is_handled == is_handled)

    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.post("/{id}/handle", response_model=EscalationRead)
async def handle_escalation(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == "CUSTOMER":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access only")

    stmt = select(Escalation).where(Escalation.id == id)
    res = await db.execute(stmt)
    esc = res.scalar_one_or_none()
    if not esc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation not found")

    esc.is_handled = True
    await db.commit()
    await db.refresh(esc)
    return esc


@router.post("/check-unhandled")
async def trigger_unhandled_check(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Evaluates active conversations for the 15-minute unhandled negative conversation timeout.
    """
    results = await escalation_engine.check_unhandled_negative_conversations(db)
    return {
        "checked_at": str(escalation_engine),
        "escalations_triggered": len(results),
        "results": [r.model_dump() for r in results]
    }


@router.get("/audit-logs", response_model=List[AuditLogRead])
async def list_audit_logs(
    event_type: Optional[str] = Query(None),
    entity_name: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == "CUSTOMER":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access only")

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    if entity_name:
        stmt = stmt.where(AuditLog.entity_name == entity_name)

    res = await db.execute(stmt)
    return list(res.scalars().all())
