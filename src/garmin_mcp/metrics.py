"""Shared conversions for Garmin metrics."""

from typing import Any, Optional


def recovery_time_hours(recovery_time_minutes: Any) -> Optional[float]:
    """Convert Garmin's recoveryTime value from minutes to hours."""
    if recovery_time_minutes is None:
        return None

    try:
        return round(float(recovery_time_minutes) / 60, 1)
    except (TypeError, ValueError):
        return None
