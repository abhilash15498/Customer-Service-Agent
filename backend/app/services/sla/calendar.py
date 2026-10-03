from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional
import zoneinfo

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config


class CalendarEngine:
    """
    Business Hours, Holidays, and SLA Calendar Engine.
    Respects Dynamic Configuration and Simulated Clock.
    """

    def _get_tz(self) -> zoneinfo.ZoneInfo:
        cfg = dynamic_config.get_config()
        tz_name = getattr(cfg, "timezone", "Asia/Kolkata")
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            return zoneinfo.ZoneInfo("UTC")

    def _normalize_dt(self, dt: datetime) -> datetime:
        tz = self._get_tz()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        return dt.astimezone(tz)

    def _parse_time_str(self, time_str: str) -> time:
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))

    def is_holiday(self, dt: datetime) -> bool:
        dt = self._normalize_dt(dt)
        cfg = dynamic_config.get_config()
        date_str = dt.strftime("%Y-%m-%d")
        return date_str in cfg.holidays

    def get_day_hours(self, dt: datetime) -> Optional[List[time]]:
        dt = self._normalize_dt(dt)
        cfg = dynamic_config.get_config()
        day_name = dt.strftime("%A").lower()
        day_config = cfg.business_hours.get(day_name)
        if not day_config or len(day_config) < 2:
            return None
        return [self._parse_time_str(day_config[0]), self._parse_time_str(day_config[1])]

    def is_business_hour(self, dt: Optional[datetime] = None) -> bool:
        dt = self._normalize_dt(dt or Clock.now())

        # Check if holiday
        if self.is_holiday(dt):
            return False

        # Check day schedule
        hours = self.get_day_hours(dt)
        if not hours:
            return False

        start_time, end_time = hours[0], hours[1]
        cur_time = dt.time()
        return start_time <= cur_time < end_time

    def get_next_business_time(self, dt: Optional[datetime] = None) -> datetime:
        """
        Calculates the exact moment of the next business opening.
        If dt is already within business hours, returns dt.
        """
        dt = self._normalize_dt(dt or Clock.now())

        current = dt
        # If currently inside business hours, return immediately
        if self.is_business_hour(current):
            return current

        # Search up to 14 days ahead
        for day_offset in range(14):
            candidate_date = current.date() + timedelta(days=day_offset)
            date_str = candidate_date.strftime("%Y-%m-%d")
            cfg = dynamic_config.get_config()
            if date_str in cfg.holidays:
                continue

            day_name = candidate_date.strftime("%A").lower()
            day_config = cfg.business_hours.get(day_name)
            if not day_config or len(day_config) < 2:
                continue

            start_t = self._parse_time_str(day_config[0])
            end_t = self._parse_time_str(day_config[1])

            opening_dt = datetime.combine(candidate_date, start_t, tzinfo=current.tzinfo)

            # If today and before opening
            if day_offset == 0:
                if current.time() < start_t:
                    return opening_dt
                elif current.time() >= end_t:
                    continue
            else:
                return opening_dt

        # Fallback to tomorrow 09:00
        return current + timedelta(days=1)

    def calculate_business_minutes(self, start_dt: datetime, end_dt: datetime) -> int:
        """
        Computes elapsed business minutes between start_dt and end_dt.
        Only counts time within operating hours on non-holiday business days.
        """
        start_dt = self._normalize_dt(start_dt)
        end_dt = self._normalize_dt(end_dt)

        if end_dt <= start_dt:
            return 0

        total_minutes = 0
        current = start_dt

        # Step through in 1-minute increments for precision
        step = timedelta(minutes=1)
        while current < end_dt:
            if self.is_business_hour(current):
                total_minutes += 1
            current += step

        return total_minutes

    def add_business_minutes(self, start_dt: datetime, business_minutes: int) -> datetime:
        """
        Advances start_dt by the specified number of business minutes,
        skipping non-business hours, holidays, and weekends.
        """
        if business_minutes <= 0:
            return start_dt

        current = self._normalize_dt(start_dt)
        # If starting outside business hours, advance to next business opening
        if not self.is_business_hour(current):
            current = self.get_next_business_time(current)

        remaining_minutes = business_minutes
        step = timedelta(minutes=1)

        while remaining_minutes > 0:
            if self.is_business_hour(current):
                remaining_minutes -= 1
            current += step
            if not self.is_business_hour(current) and remaining_minutes > 0:
                current = self.get_next_business_time(current)

        return current

    def calculate_sla_targets(
        self,
        priority: str,
        start_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates target resolution minutes and deadlines based on priority and calendar.
        """
        if start_dt is None:
            start_dt = Clock.now()

        cfg = dynamic_config.get_config()
        default_hours = cfg.sla_config.get("default_resolution_hours", 24)
        multipliers = cfg.sla_config.get("priority_multipliers", {
            "LOW": 2.0,
            "MEDIUM": 1.0,
            "HIGH": 0.5,
            "CRITICAL": 0.25
        })

        multiplier = multipliers.get(priority.upper(), 1.0)
        resolution_minutes = int(default_hours * 60 * multiplier)
        # First response target is 10% of resolution time or minimum 15 minutes
        response_minutes = max(15, int(resolution_minutes * 0.1))

        resolution_deadline = self.add_business_minutes(start_dt, resolution_minutes)
        response_deadline = self.add_business_minutes(start_dt, response_minutes)

        return {
            "priority": priority.upper(),
            "target_response_minutes": response_minutes,
            "target_resolution_minutes": resolution_minutes,
            "response_deadline": response_deadline,
            "resolution_deadline": resolution_deadline,
        }


calendar_engine = CalendarEngine()
