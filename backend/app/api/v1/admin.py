from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_db
from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.schemas.config import DynamicConfigUpdate, TimeMachineRequest

router = APIRouter(prefix="/admin", tags=["Admin & System Configuration"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Deep health check inspecting Database, Clock, and System Status."""
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "simulated_clock": {
            "active": Clock.is_simulated(),
            "current_time": Clock.now().isoformat()
        },
        "version": "1.0.0"
    }


@router.get("/config")
async def get_runtime_configuration(current_admin=Depends(get_current_admin)):
    """Retrieve full dynamic business configuration."""
    return dynamic_config.get_config().model_dump()


@router.put("/config")
async def update_runtime_configuration(
    update_data: DynamicConfigUpdate,
    current_admin=Depends(get_current_admin)
):
    """
    Update business rules, thresholds, hours, or SLA parameters dynamically.
    No application restart required.
    """
    updated = dynamic_config.update_config(update_data.updates)
    return {
        "message": "Configuration updated successfully",
        "configuration": updated.model_dump()
    }


@router.post("/config/reset")
async def reset_runtime_configuration(current_admin=Depends(get_current_admin)):
    """Reset configuration back to baseline factory defaults."""
    dynamic_config.reset_to_defaults()
    return {"message": "Configuration reset to defaults"}


@router.get("/time-machine")
async def get_current_system_time():
    """Check whether simulated clock is currently active and current time value."""
    return {
        "is_simulated": Clock.is_simulated(),
        "time": Clock.now().isoformat(),
        "timestamp_epoch": Clock.now().timestamp()
    }


@router.post("/time-machine")
async def set_simulated_time(
    req: TimeMachineRequest,
    current_admin=Depends(get_current_admin)
):
    """
    Override current time with simulated timestamp.
    Used for hidden evaluation tests (e.g., weekends, holidays, after-hours, SLA breach).
    Passing null resets to real server clock.
    """
    if req.simulated_time:
        Clock.set_time(req.simulated_time)
        return {
            "message": "Simulated time activated",
            "simulated_time": Clock.now().isoformat()
        }
    else:
        Clock.reset()
        return {
            "message": "Simulated time cleared, reverted to real clock",
            "real_time": Clock.now().isoformat()
        }
