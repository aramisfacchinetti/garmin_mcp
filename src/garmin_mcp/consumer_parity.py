"""Bounded read-only wrappers for upstream Garmin consumer-client capabilities."""

import datetime
import json
from typing import Any


garmin_client = None

MAX_ACTIVITY_DETAIL_CHART = 2000
MAX_ACTIVITY_DETAIL_POLYLINE = 4000
MAX_GEAR_ACTIVITY_LIMIT = 1000
MAX_GOLF_SUMMARY_LIMIT = 1000


class _CapabilityUnavailable(Exception):
    """Raised when the installed client does not provide an upstream method."""


def configure(client):
    """Configure the module with the shared Garmin client instance."""
    global garmin_client
    garmin_client = client


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer from {minimum} to {maximum}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be an integer from {minimum} to {maximum}"
        ) from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be an integer from {minimum} to {maximum}")
    return parsed


def _date(value: str, field: str = "date") -> str:
    try:
        datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD format") from exc
    return value


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _invoke(method: str, *args: Any, **kwargs: Any) -> Any:
    if garmin_client is None:
        raise _CapabilityUnavailable("Garmin client is not configured")
    function = getattr(garmin_client, method, None)
    if function is None or not callable(function):
        raise _CapabilityUnavailable(
            f"The installed Garmin client does not provide {method}"
        )
    return function(*args, **kwargs)


def _success(method: str, data: Any, **metadata: Any) -> str:
    payload = {
        "status": "ok" if data is not None else "not_available",
        "method": method,
        **metadata,
        "data": data,
    }
    return json.dumps(payload, indent=2, default=str)


def _failure(method: str, error: Exception, status: str = "error") -> str:
    return json.dumps(
        {
            "status": status,
            "method": method,
            "error": str(error),
        },
        indent=2,
    )


def _run(method: str, *args: Any, **kwargs: Any) -> str:
    try:
        return _success(method, _invoke(method, *args, **kwargs))
    except _CapabilityUnavailable as exc:
        return _failure(method, exc, status="unavailable")
    except Exception as exc:
        return _failure(method, exc)


def register_tools(app):
    """Register read-only consumer-client parity tools."""

    @app.tool()
    async def get_activity_details(
        activity_id: int,
        max_chart: int = 500,
        max_polyline: int = 1000,
    ) -> str:
        """Get bounded chart and polyline details for one activity.

        The upstream endpoint applies the requested chart and polyline limits.
        The raw response remains under ``data`` and the applied limits are
        returned alongside it so callers can distinguish bounded data from a
        complete unbounded export.
        """
        method = "get_activity_details"
        try:
            activity_id = _positive_int(activity_id, "activity_id")
            max_chart = _bounded_int(
                max_chart, "max_chart", 0, MAX_ACTIVITY_DETAIL_CHART
            )
            max_polyline = _bounded_int(
                max_polyline, "max_polyline", 0, MAX_ACTIVITY_DETAIL_POLYLINE
            )
            data = _invoke(
                method,
                str(activity_id),
                maxchart=max_chart,
                maxpoly=max_polyline,
            )
            return _success(
                method,
                data,
                activity_id=activity_id,
                limits={"max_chart": max_chart, "max_polyline": max_polyline},
            )
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_available_badges() -> str:
        """Get the user's available badge catalog."""
        return _run("get_available_badges")

    @app.tool()
    async def get_in_progress_badges() -> str:
        """Get badges currently in progress for the user."""
        return _run("get_in_progress_badges")

    @app.tool()
    async def get_gear_activities(gear_uuid: str, limit: int = 1000) -> str:
        """Get activities associated with one gear item, with a bounded limit."""
        method = "get_gear_activities"
        try:
            gear_uuid = _required_text(gear_uuid, "gear_uuid")
            limit = _bounded_int(limit, "limit", 1, MAX_GEAR_ACTIVITY_LIMIT)
            return _success(method, _invoke(method, gear_uuid, limit=limit), limit=limit)
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_gear_defaults(user_profile_number: str) -> str:
        """Get the account's default gear assignments by activity type."""
        method = "get_gear_defaults"
        try:
            return _success(
                method,
                _invoke(method, _required_text(user_profile_number, "user_profile_number")),
            )
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_gear_stats(gear_uuid: str) -> str:
        """Get usage statistics for one gear item."""
        method = "get_gear_stats"
        try:
            return _success(method, _invoke(method, _required_text(gear_uuid, "gear_uuid")))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_golf_summary(start: int = 0, limit: int = 100) -> str:
        """Get a bounded list of golf scorecard summaries."""
        method = "get_golf_summary"
        try:
            start = _bounded_int(start, "start", 0, 100000)
            limit = _bounded_int(limit, "limit", 1, MAX_GOLF_SUMMARY_LIMIT)
            return _success(method, _invoke(method, start=start, limit=limit), start=start, limit=limit)
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_golf_scorecard(scorecard_id: int) -> str:
        """Get one golf scorecard by ID."""
        method = "get_golf_scorecard"
        try:
            return _success(method, _invoke(method, _positive_int(scorecard_id, "scorecard_id")))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_golf_shot_data(
        scorecard_id: int,
        hole_numbers: str = "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18",
    ) -> str:
        """Get golf shot data for selected holes in a scorecard."""
        method = "get_golf_shot_data"
        try:
            scorecard_id = _positive_int(scorecard_id, "scorecard_id")
            holes = []
            for raw_hole in hole_numbers.split(","):
                hole = _bounded_int(raw_hole.strip(), "hole_numbers", 1, 18)
                if hole not in holes:
                    holes.append(hole)
            if not holes:
                raise ValueError("hole_numbers must contain at least one hole")
            normalized_holes = ",".join(str(hole) for hole in holes)
            return _success(
                method,
                _invoke(method, scorecard_id, hole_numbers=normalized_holes),
                hole_numbers=normalized_holes,
            )
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except (AttributeError, TypeError, ValueError) as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_intensity_minutes_data(date: str) -> str:
        """Get raw daily intensity-minutes data for a date."""
        method = "get_intensity_minutes_data"
        try:
            return _success(method, _invoke(method, _date(date)))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_last_activity() -> str:
        """Get the most recent activity returned by Garmin Connect."""
        return _run("get_last_activity")

    @app.tool()
    async def get_max_metrics(date: str) -> str:
        """Get maximum-metric data for a date."""
        method = "get_max_metrics"
        try:
            return _success(method, _invoke(method, _date(date)))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_running_tolerance(
        start_date: str,
        end_date: str,
        aggregation: str = "weekly",
    ) -> str:
        """Get running-tolerance data for a date range."""
        method = "get_running_tolerance"
        try:
            start_date = _date(start_date, "start_date")
            end_date = _date(end_date, "end_date")
            if aggregation not in {"daily", "weekly"}:
                raise ValueError("aggregation must be 'daily' or 'weekly'")
            return _success(
                method,
                _invoke(method, start_date, end_date, aggregation=aggregation),
                start_date=start_date,
                end_date=end_date,
                aggregation=aggregation,
            )
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_scheduled_workout_by_id(scheduled_workout_id: int) -> str:
        """Get one scheduled workout by its calendar ID."""
        method = "get_scheduled_workout_by_id"
        try:
            return _success(
                method,
                _invoke(method, _positive_int(scheduled_workout_id, "scheduled_workout_id")),
            )
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_training_plans() -> str:
        """Get all available Garmin training plans."""
        return _run("get_training_plans")

    @app.tool()
    async def get_training_plan_by_id(plan_id: int) -> str:
        """Get phased training-plan details by plan ID."""
        method = "get_training_plan_by_id"
        try:
            return _success(method, _invoke(method, _positive_int(plan_id, "plan_id")))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    @app.tool()
    async def get_adaptive_training_plan_by_id(plan_id: int) -> str:
        """Get adaptive training-plan details by plan ID."""
        method = "get_adaptive_training_plan_by_id"
        try:
            return _success(method, _invoke(method, _positive_int(plan_id, "plan_id")))
        except _CapabilityUnavailable as exc:
            return _failure(method, exc, status="unavailable")
        except ValueError as exc:
            return _failure(method, exc, status="invalid_argument")
        except Exception as exc:
            return _failure(method, exc)

    return app
