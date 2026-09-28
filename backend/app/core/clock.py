from datetime import datetime, timezone
from typing import Optional


class SimulatedClock:
    """
    Time provider for the system.
    Supports injecting simulated time offsets or static simulated timestamps
    for hidden evaluation scenarios (e.g., weekend simulation, holiday simulation,
    historical policy checks, SLA breaches) without mutating host OS clock.
    """
    _simulated_time: Optional[datetime] = None

    @classmethod
    def now(cls) -> datetime:
        if cls._simulated_time is not None:
            return cls._simulated_time
        return datetime.now(timezone.utc)

    @classmethod
    def set_time(cls, dt: datetime) -> None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cls._simulated_time = dt

    @classmethod
    def reset(cls) -> None:
        cls._simulated_time = None

    @classmethod
    def is_simulated(cls) -> bool:
        return cls._simulated_time is not None


Clock = SimulatedClock
