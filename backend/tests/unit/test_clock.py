from datetime import datetime, timezone
import pytest
from app.core.clock import Clock


def test_real_clock_by_default():
    Clock.reset()
    assert not Clock.is_simulated()
    now1 = Clock.now()
    now_real = datetime.now(timezone.utc)
    # Difference should be under 1 second
    assert abs((now1 - now_real).total_seconds()) < 1.0


def test_simulated_time_override():
    # Scenario 9: Simulated time changes
    target_dt = datetime(2026, 12, 25, 14, 30, 0, tzinfo=timezone.utc)
    Clock.set_time(target_dt)

    assert Clock.is_simulated()
    assert Clock.now() == target_dt
    assert Clock.now().year == 2026
    assert Clock.now().month == 12
    assert Clock.now().day == 25

    # Reset
    Clock.reset()
    assert not Clock.is_simulated()
    assert Clock.now().year >= 2026
