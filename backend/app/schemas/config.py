from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel


class DynamicConfigUpdate(BaseModel):
    updates: Dict[str, Any]


class TimeMachineRequest(BaseModel):
    simulated_time: Optional[datetime] = None  # None to reset to real time
