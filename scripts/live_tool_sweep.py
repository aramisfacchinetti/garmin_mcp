#!/usr/bin/env python3
"""Run a live sweep over the registered Garmin MCP tools.

This is intentionally a repo-local diagnostic helper, not a pytest test:
it talks to the real Garmin account and many tools legitimately return
"no data" depending on the account. By default it skips mutating tools.
Use --include-mutations only when you are ready to create/update/delete data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from garminconnect import Garmin
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_analysis
from garmin_mcp import activity_management
from garmin_mcp import challenges
from garmin_mcp import courses
from garmin_mcp import consumer_parity
from garmin_mcp import consumer_writes
from garmin_mcp import calendar_events
from garmin_mcp import data_management
from garmin_mcp import devices
from garmin_mcp import gear_management
from garmin_mcp import health_wellness
from garmin_mcp import metric_catalog
from garmin_mcp import nutrition
from garmin_mcp import training
from garmin_mcp import user_profile
from garmin_mcp import weight_management
from garmin_mcp import womens_health
from garmin_mcp import workout_builders
from garmin_mcp import workout_templates
from garmin_mcp import workouts
from garmin_mcp.token_utils import get_token_path, validate_tokens


MODULES = [
    activity_management,
    health_wellness,
    user_profile,
    devices,
    gear_management,
    weight_management,
    challenges,
    training,
    workouts,
    data_management,
    womens_health,
    nutrition,
    workout_builders,
    courses,
    activity_analysis,
    consumer_parity,
    consumer_writes,
    calendar_events,
]

MUTATING_TOOLS = {
    "add_body_composition",
    "add_gear_to_activity",
    "add_hydration_data",
    "add_weigh_in",
    "add_weigh_in_with_timestamps",
    "create_custom_food",
    "create_manual_activity_from_json",
    "create_manual_activity",
    "create_run_workout",
    "create_strength_workout",
    "create_walk_run_workout",
    "create_z2_walk_workout",
    "delete_course",
    "delete_activity",
    "delete_blood_pressure",
    "delete_custom_food",
    "delete_food_log",
    "delete_weigh_in",
    "delete_weigh_ins",
    "delete_workout",
    "delete_workouts",
    "log_custom_food",
    "log_food",
    "import_activity",
    "remove_gear_from_activity",
    "request_reload",
    "schedule_week",
    "schedule_workout",
    "schedule_workouts",
    "set_activity_exercise_sets",
    "set_gear_default",
    "set_activity_description",
    "set_activity_event_type",
    "set_activity_feel",
    "set_activity_name",
    "set_activity_type",
    "set_blood_pressure",
    "set_fit_download_dir",
    "set_nutrition_daily_settings",
    "set_perceived_effort",
    "unschedule_workout",
    "unschedule_workouts",
    "update_custom_food",
    "upload_course",
    "upload_activity",
    "upload_workout",
    "upload_workouts",
    "upsert_and_log",
}

DESTRUCTIVE_TOOLS_REQUIRING_SWEEP_CREATED_IDS = {
    "delete_course",
    "delete_activity",
    "delete_blood_pressure",
    "delete_food_log",
    "delete_weigh_in",
    "delete_workout",
    "delete_workouts",
    "delete_weigh_ins",
}

CREATION_TOOLS_THAT_PRODUCE_CLEANUP_TARGETS = {
    "create_run_workout",
    "create_strength_workout",
    "create_walk_run_workout",
    "create_z2_walk_workout",
    "upload_course",
    "upload_activity",
    "upload_workout",
    "upload_workouts",
}

EXPECTED_NO_DATA_PREFIXES = (
    "No ",
)

RETRYABLE_UPSTREAM_MARKERS = (
    "api error 504",
    "error 504: gateway time-out",
    "origin_gateway_timeout",
)

ACCOUNT_LIMITED_TOOLS = {
    "get_custom_food_serving_units",
    "get_custom_foods",
}

SWEEP_CUSTOM_FOOD_NAME_PREFIX = "garmin mcp live sweep food"
SWEEP_QUICK_ADD_NAME_PREFIX = "garmin mcp live sweep quick add"

MISSING_GEAR_UUID = "00000000-0000-0000-0000-000000000000"


@dataclass
class SweepContext:
    today: str
    start_date: str
    end_date: str
    future_date: str
    quick_add_food_name: str
    custom_food_name: str
    activity_id: int | None = None
    activity_name: str | None = None
    device_id: str | None = None
    user_profile_number: str | None = None
    training_plan_id: int | str | None = None
    adaptive_plan_id: int | str | None = None
    workout_id: int | str | None = None
    course_id: int | None = None
    gear_uuid: str | None = None
    food_id: str | None = None
    serving_id: str | None = None
    food_log_id: int | None = None
    generated_gpx_path: str | None = None
    can_delete_today_weigh_ins: bool = False
    created_workout_ids: list[int | str] = field(default_factory=list)
    created_course_ids: list[int] = field(default_factory=list)
    created_food_log_ids: list[int] = field(default_factory=list)
    created_weigh_in_dates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_app(client: Any) -> FastMCP:
    app = FastMCP("Garmin live sweep")
    for module in MODULES:
        module.configure(client)
        app = module.register_tools(app)
    app = metric_catalog.register_tools(app)
    app = workout_templates.register_resources(app)
    return app


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, indent=2)

    # FastMCP.call_tool returns (content_items, metadata). Only the first
    # element is the user-visible tool response that should drive status.
    if (
        isinstance(content, tuple)
        and len(content) == 2
        and isinstance(content[0], list)
    ):
        return content_to_text(content[0])

    if isinstance(content, (list, tuple)):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            elif isinstance(item, (list, tuple, dict)):
                parts.append(content_to_text(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def first_list_item(value: Any, *keys: str) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        for key in keys:
            child = value.get(key)
            if isinstance(child, list) and child:
                return child[0]
    return None


def extract_workouts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [w for w in value if isinstance(w, dict)]
    if isinstance(value, dict):
        for key in ("workouts", "workoutDTOs", "workoutList", "items"):
            child = value.get(key)
            if isinstance(child, list):
                return [w for w in child if isinstance(w, dict)]
    return []


def append_unique(values: list[Any], value: Any) -> None:
    if value is not None and value not in values:
        values.append(value)


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def collect_food_log_ids(client: Garmin, ctx: SweepContext) -> None:
    sweep_food_names = {
        ctx.quick_add_food_name.lower(),
        ctx.custom_food_name.lower(),
    }
    try:
        food_log = client.connectapi(f"/nutrition-service/food/logs/{ctx.today}")
    except Exception as exc:
        ctx.notes.append(f"food log cleanup context unavailable: {exc}")
        return

    for item in iter_dicts(food_log):
        text = " ".join(str(value).lower() for value in item.values())
        if not any(name in text for name in sweep_food_names):
            continue
        log_id = item.get("logId") or item.get("foodLogId")
        if log_id is None:
            continue
        try:
            append_unique(ctx.created_food_log_ids, int(log_id))
        except (TypeError, ValueError):
            continue


def cleanup_blood_pressure(client: Garmin, ctx: SweepContext, measurement: dict[str, Any]) -> None:
    version = measurement.get("version")
    if version is None:
        try:
            data = client.get_blood_pressure(ctx.today, ctx.today)
        except Exception as exc:
            ctx.notes.append(f"blood pressure cleanup context unavailable: {exc}")
            return

        target_timestamp = measurement.get("measurementTimestampLocal")
        for summary in data.get("measurementSummaries", []) if isinstance(data, dict) else []:
            for item in summary.get("measurements", []):
                if target_timestamp and item.get("measurementTimestampLocal") == target_timestamp:
                    version = item.get("version")
                    break
            if version is not None:
                break

    if version is None:
        ctx.notes.append("blood pressure cleanup skipped: created version not found")
        return

    try:
        client.delete_blood_pressure(str(version), ctx.today)
    except Exception as exc:
        ctx.notes.append(f"blood pressure cleanup failed: {exc}")


def minimal_workout(name: str) -> dict[str, Any]:
    return {
        "workoutName": name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "endCondition": {
                            "conditionTypeId": 2,
                            "conditionTypeKey": "time",
                        },
                        "endConditionValue": 60.0,
                        "targetType": {
                            "workoutTargetTypeId": 1,
                            "workoutTargetTypeKey": "no.target",
                        },
                    }
                ],
            }
        ],
    }


def write_tiny_gpx(path: str) -> None:
    Path(path).write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="garmin-mcp-live-sweep" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>garmin mcp live sweep</name><trkseg>
    <trkpt lat="47.3769" lon="8.5417"><ele>408</ele><time>2026-05-17T10:00:00Z</time></trkpt>
    <trkpt lat="47.3771" lon="8.5420"><ele>410</ele><time>2026-05-17T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
""",
        encoding="utf-8",
    )


def collect_context(client: Garmin, args: argparse.Namespace) -> SweepContext:
    today = args.date or date.today().isoformat()
    end = datetime.strptime(today, "%Y-%m-%d").date()
    start = (end - timedelta(days=args.days_back)).isoformat()
    future = (end + timedelta(days=30)).isoformat()
    run_stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ctx = SweepContext(
        today=today,
        start_date=start,
        end_date=today,
        future_date=future,
        quick_add_food_name=f"{SWEEP_QUICK_ADD_NAME_PREFIX} {run_stamp}",
        custom_food_name=f"{SWEEP_CUSTOM_FOOD_NAME_PREFIX} {run_stamp}",
    )

    try:
        activities = client.get_activities(0, 10)
        first = first_list_item(activities)
        if isinstance(first, dict):
            ctx.activity_id = first.get("activityId")
            ctx.activity_name = first.get("activityName")
    except Exception as exc:
        ctx.notes.append(f"activity context unavailable: {exc}")

    try:
        device = client.get_device_last_used()
        if isinstance(device, dict):
            ctx.user_profile_number = str(device.get("userProfileNumber") or "") or None
            ctx.device_id = str(
                device.get("userDeviceId") or device.get("deviceId") or ""
            ) or None
    except Exception as exc:
        ctx.notes.append(f"device context unavailable: {exc}")

    try:
        workout_items = extract_workouts(client.get_workouts())
        if workout_items:
            ctx.workout_id = workout_items[0].get("workoutId") or workout_items[0].get(
                "workoutId"
            )
    except Exception as exc:
        ctx.notes.append(f"workout context unavailable: {exc}")

    try:
        courses_data = client.client.request(
            "GET", "connectapi", "/course-service/course"
        ).json()
        first_course = first_list_item(courses_data)
        if isinstance(first_course, dict):
            ctx.course_id = first_course.get("courseId")
    except Exception as exc:
        ctx.notes.append(f"course context unavailable: {exc}")

    try:
        weigh_ins = client.get_daily_weigh_ins(ctx.today)
        existing = (
            weigh_ins.get("dateWeightList", [])
            if isinstance(weigh_ins, dict)
            else []
        )
        ctx.can_delete_today_weigh_ins = not existing
        if existing:
            ctx.notes.append(
                "weight mutation cleanup disabled: existing weigh-ins found for today"
            )
    except Exception as exc:
        ctx.notes.append(f"weight cleanup context unavailable: {exc}")

    try:
        profile = client.get_device_last_used()
        user_profile_id = profile.get("userProfileNumber") if isinstance(profile, dict) else None
        if user_profile_id:
            gear = client.get_gear(user_profile_id)
            first_gear = first_list_item(gear)
            if isinstance(first_gear, dict):
                ctx.gear_uuid = first_gear.get("uuid")
    except Exception as exc:
        ctx.notes.append(f"gear context unavailable: {exc}")

    try:
        plans = client.get_training_plans()
        plan_items = plans.get("trainingPlanList", []) if isinstance(plans, dict) else []
        for plan in plan_items:
            if not isinstance(plan, dict):
                continue
            plan_id = plan.get("trainingPlanId")
            if ctx.training_plan_id is None:
                ctx.training_plan_id = plan_id
            category = str(plan.get("trainingPlanCategory") or "").upper()
            if ctx.adaptive_plan_id is None and "ADAPTIVE" in category:
                ctx.adaptive_plan_id = plan_id
    except Exception as exc:
        ctx.notes.append(f"training-plan context unavailable: {exc}")

    if args.include_mutations:
        fd, path = tempfile.mkstemp(prefix="garmin-mcp-live-sweep-", suffix=".gpx")
        os.close(fd)
        write_tiny_gpx(path)
        ctx.generated_gpx_path = path

    return ctx


def dependency(name: str, value: Any) -> tuple[None, str] | None:
    if value is None:
        return None, f"missing dependency: {name}"
    return None


def arguments_for(tool_name: str, ctx: SweepContext) -> tuple[dict[str, Any] | None, str | None]:
    name = tool_name
    one_day = {"date": ctx.today}
    date_range = {"start_date": ctx.start_date, "end_date": ctx.end_date}
    bounded_range = {"start_date": ctx.end_date, "end_date": ctx.end_date}
    schedule_workout_id = ctx.created_workout_ids[0] if ctx.created_workout_ids else None
    actual_today = date.today().isoformat()

    if name in {
        "add_body_composition",
        "add_weigh_in",
        "add_weigh_in_with_timestamps",
    } and not ctx.can_delete_today_weigh_ins:
        return None, "missing dependency: safe empty weigh-in cleanup date"

    if name == "add_weigh_in" and ctx.today != actual_today:
        return None, "missing dependency: add_weigh_in only records on current date"

    if name == "set_blood_pressure" and ctx.today != actual_today:
        return None, "missing dependency: set_blood_pressure only records on current date"

    if name in {"create_custom_food", "delete_custom_food", "upsert_and_log"}:
        return None, "missing dependency: custom food cleanup unavailable"

    if name == "create_manual_activity":
        return None, "missing dependency: manual activity cleanup unavailable"

    if name == "set_activity_name":
        if not ctx.activity_name:
            return None, "missing dependency: activity_name"
        return None, "missing dependency: existing activity mutation is not sweep-owned"

    if name in {
        "set_activity_description",
        "set_activity_event_type",
        "set_activity_feel",
        "set_activity_type",
        "set_perceived_effort",
    }:
        return None, "missing dependency: existing activity mutation is not sweep-owned"

    if name == "set_fit_download_dir":
        return None, "missing dependency: persistent FIT directory mutation is not sweep-owned"

    if name == "set_nutrition_daily_settings":
        return None, "missing dependency: nutrition-goal mutation is not sweep-owned"

    if name in {"unschedule_workout", "unschedule_workouts"}:
        return None, "missing dependency: scheduled workout cleanup is not sweep-owned"

    if name in {
        "create_manual_activity_from_json",
        "delete_activity",
        "delete_blood_pressure",
        "delete_weigh_in",
        "import_activity",
        "set_activity_exercise_sets",
        "set_gear_default",
        "upload_activity",
    }:
        return None, "missing dependency: live cleanup is not sweep-owned"

    if name in {"get_golf_scorecard", "get_golf_shot_data"}:
        return None, "missing dependency: scorecard_id"

    if name == "download_course_gpx":
        return None, "missing dependency: local course-download output is not sweep-owned"

    if name == "get_scheduled_workout_by_id":
        return None, "missing dependency: scheduled_workout_id"

    explicit: dict[str, dict[str, Any]] = {
        "add_body_composition": {"date": ctx.today, "weight": 70.0, "confirm": True},
        "add_hydration_data": {
            "value_in_ml": 0,
            "cdate": ctx.today,
            "timestamp": f"{ctx.today}T12:00:00.000",
            "confirm": True,
        },
        "add_weigh_in": {"weight": 70.0, "unit_key": "kg", "confirm": True},
        "add_weigh_in_with_timestamps": {
            "weight": 70.0,
            "unit_key": "kg",
            "date_timestamp": f"{ctx.today}T12:00:00.000",
            "gmt_timestamp": f"{ctx.today}T10:00:00.000",
            "confirm": True,
        },
        "count_activities": {},
        "create_strength_workout": {
            "name": "garmin mcp live sweep strength",
            "exercises": [{"name": "Push Up", "sets": 1, "reps": 1, "rest_seconds": 5}],
            "confirm": True,
        },
        "create_walk_run_workout": {
            "name": "garmin mcp live sweep walk run",
            "run_seconds": 30,
            "walk_seconds": 30,
            "repeats": 1,
            "warmup_min": 1,
            "cooldown_min": 1,
            "confirm": True,
        },
        "create_run_workout": {
            "name": "garmin mcp live sweep run",
            "run_seconds": 30,
            "warmup_min": 1,
            "cooldown_min": 1,
            "hr_zone": "Z2",
            "confirm": True,
        },
        "create_z2_walk_workout": {
            "name": "garmin mcp live sweep z2 walk",
            "duration_min": 5,
            "hr_min": 110,
            "hr_max": 130,
            "confirm": True,
        },
        "get_activities": {"start": 0, "limit": 5},
        "get_activities_by_date": {**date_range, "activity_type": ""},
        "get_activities_fordate": one_day,
        "get_activity_types": {},
        "get_adhoc_challenges": {"start": 0, "limit": 5},
        "get_available_badge_challenges": {"start": 1, "limit": 5},
        "get_badge_challenges": {"start": 1, "limit": 5},
        "get_non_completed_badge_challenges": {"start": 1, "limit": 5},
        "get_inprogress_virtual_challenges": {"start": 1, "limit": 5},
        "get_all_day_events": one_day,
        "get_all_day_stress": one_day,
        "get_acclimation": one_day,
        "get_blood_pressure": date_range,
        "get_body_battery": date_range,
        "get_body_battery_events": one_day,
        "get_body_composition": date_range,
        "get_calendar_events": {
            "start_date": ctx.end_date,
            "end_date": ctx.end_date,
        },
        "get_courses": {},
        "get_custom_food_serving_units": {},
        "get_custom_foods": {"search": "", "start": 0, "limit": 5},
        "get_cycling_ftp": {},
        "get_daily_steps": date_range,
        "get_daily_weigh_ins": one_day,
        "get_device_alarms": {},
        "get_device_last_used": {},
        "get_devices": {},
        "get_earned_badges": {},
        "get_endurance_score": date_range,
        "get_fitnessage_data": {"date": ctx.today, "details": False},
        "get_floors": one_day,
        "get_full_name": {},
        "get_garmin_coach_workouts": {"calendar_date": ctx.today},
        "get_gear": {"include_stats": True},
        "get_golf_summary": {},
        "get_goals": {"goal_type": "active"},
        "get_heart_rates": one_day,
        "get_heart_rates_summary": one_day,
        "get_heart_rate_zones": {},
        "get_hill_score": date_range,
        "get_hrv_data": {"date": ctx.today, "return_timeseries": False},
        "get_hrv_trend": date_range,
        "get_hydration_data": one_day,
        "get_lactate_threshold": {},
        "get_last_activity": {},
        "get_lifestyle_logging_data": one_day,
        "get_menstrual_calendar_data": date_range,
        "get_menstrual_data_for_date": one_day,
        "get_morning_training_readiness": one_day,
        "get_metric_catalog": {},
        "get_intensity_minutes_data": one_day,
        "get_max_metrics": one_day,
        "get_nutrition_daily_food_log": one_day,
        "get_nutrition_daily_meals": one_day,
        "get_nutrition_daily_settings": one_day,
        "get_nutrition_summary_between_dates": bounded_range,
        "get_personal_record": {},
        "get_power_duration_curve": {"num_activities": 10, "activity_type": "cycling"},
        "get_pregnancy_summary": {},
        "get_primary_training_device": {},
        "get_progress_summary_between_dates": {**date_range, "metric": "duration"},
        "get_race_predictions": {},
        "get_respiration_data": one_day,
        "get_respiration_summary": one_day,
        "get_respiration_trend": date_range,
        "get_rhr_day": one_day,
        "get_scheduled_workouts": date_range,
        "search_foods": {"query": "", "start": 0, "limit": 5},
        "get_sleep_data": one_day,
        "get_sleep_summary": one_day,
        "get_sleep_summary_range": bounded_range,
        "get_spo2_data": one_day,
        "get_stats": one_day,
        "get_stats_and_body": one_day,
        "get_stats_range": bounded_range,
        "get_steps_data": one_day,
        "get_stress_data": one_day,
        "get_stress_summary": one_day,
        "get_training_load_trend": date_range,
        "get_training_load_balance": {"date": ctx.today},
        "get_energy_balance": bounded_range,
        "get_training_plan_workouts": {"calendar_date": ctx.today},
        "get_training_plans": {},
        "get_training_readiness": one_day,
        "get_training_status": one_day,
        "get_unit_system": {},
        "get_user_profile": {},
        "get_user_summary": one_day,
        "get_userprofile_settings": {},
        "get_vo2max_trend": date_range,
        "get_weekly_intensity_minutes": {"end_date": ctx.end_date, "weeks": 4},
        "get_weekly_steps": {"end_date": ctx.end_date, "weeks": 4},
        "get_weekly_stress": {"end_date": ctx.end_date, "weeks": 4},
        "get_weigh_ins": date_range,
        "get_workouts": {},
        "log_food": {
            "meal_date": ctx.today,
            "meal_time": "12:00:00",
            "name": ctx.quick_add_food_name,
            "calories": 1,
            "carbs": 0,
            "protein": 0,
            "fat": 0,
            "confirm": True,
        },
        "request_reload": one_day,
        "set_blood_pressure": {"systolic": 120, "diastolic": 80, "pulse": 60, "confirm": True},
        "upload_workout": {
            "workout_data": minimal_workout("garmin mcp live sweep"),
            "confirm": True,
        },
        "upload_workouts": {
            "workouts": [minimal_workout("garmin mcp live sweep batch")],
            "confirm": True,
        },
    }

    if name == "delete_course":
        if not ctx.created_course_ids:
            return None, "missing dependency: sweep-created course_id"
        return {"course_id": ctx.created_course_ids.pop(0), "confirm": True}, None

    if name == "delete_food_log":
        if not ctx.created_food_log_ids:
            return {"log_id": -1, "meal_date": ctx.today, "confirm": True}, None
        return {"log_id": ctx.created_food_log_ids.pop(0), "meal_date": ctx.today, "confirm": True}, None

    if name == "delete_weigh_ins":
        if not ctx.created_weigh_in_dates:
            return None, "missing dependency: sweep-created weigh-in date"
        return {"date": ctx.created_weigh_in_dates.pop(0), "delete_all": True, "confirm": True}, None

    if name == "delete_workout":
        if not ctx.created_workout_ids:
            return None, "missing dependency: sweep-created workout_id"
        return {"workout_id": ctx.created_workout_ids.pop(0), "confirm": True}, None

    if name == "delete_workouts":
        if not ctx.created_workout_ids:
            return None, "missing dependency: sweep-created workout_ids"
        workout_ids = list(ctx.created_workout_ids)
        ctx.created_workout_ids.clear()
        return {"workout_ids": workout_ids, "confirm": True}, None

    if name == "get_gear_defaults":
        missing = dependency("user_profile_number", ctx.user_profile_number)
        if missing:
            return missing
        return {"user_profile_number": ctx.user_profile_number}, None

    if name in explicit:
        return explicit[name], None

    if name == "get_activity_details":
        missing = dependency("activity_id", ctx.activity_id)
        if missing:
            return missing
        return {
            "activity_id": ctx.activity_id,
            "max_chart": 100,
            "max_polyline": 100,
        }, None

    if name == "get_course_details":
        missing = dependency("course_id", ctx.course_id)
        if missing:
            return missing
        return {"course_id": ctx.course_id}, None

    if name == "get_running_tolerance_trend":
        return {**bounded_range, "aggregation": "weekly"}, None

    if name == "get_running_tolerance":
        return {
            "start_date": ctx.start_date,
            "end_date": ctx.end_date,
            "aggregation": "weekly",
        }, None

    if name in {"get_available_badges", "get_in_progress_badges"}:
        return {}, None

    if name in {"get_gear_activities", "get_gear_stats"}:
        missing = dependency("gear_uuid", ctx.gear_uuid)
        if missing:
            return missing
        return {"gear_uuid": ctx.gear_uuid}, None

    if name in {"get_training_plan_by_id", "get_adaptive_training_plan_by_id"}:
        plan_id = (
            ctx.adaptive_plan_id
            if name == "get_adaptive_training_plan_by_id"
            else ctx.training_plan_id
        )
        missing = dependency("training_plan_id", plan_id)
        if missing:
            return missing
        return {"plan_id": plan_id}, None

    if name in {
        "get_activity",
        "get_activity_exercise_sets",
        "get_activity_fit_data",
        "get_activity_fit_messages",
        "get_activity_gear",
        "get_activity_hr_in_timezones",
        "get_activity_power_in_timezones",
        "get_activity_split_summaries",
        "get_activity_splits",
        "get_activity_typed_splits",
        "get_activity_weather",
        "get_training_effect",
    }:
        missing = dependency("activity_id", ctx.activity_id)
        if missing:
            return missing
        args: dict[str, Any] = {"activity_id": ctx.activity_id}
        if name == "get_activity_fit_data":
            args["include_records"] = False
        elif name == "get_activity_fit_messages":
            args.update({"include_records": False, "message_limit": 100})
        return args, None

    if name == "set_activity_name":
        missing = dependency("activity_id", ctx.activity_id)
        if missing:
            return missing
        if not ctx.activity_name:
            return None, "missing dependency: activity_name"
        return {"activity_id": ctx.activity_id, "activity_name": ctx.activity_name}, None

    if name in {"add_gear_to_activity", "remove_gear_from_activity"}:
        if ctx.activity_id is None:
            return None, "missing dependency: activity_id"
        return {"activity_id": ctx.activity_id, "gear_uuid": MISSING_GEAR_UUID, "confirm": True}, None

    if name in {"get_device_settings", "get_device_solar_data"}:
        missing = dependency("device_id", ctx.device_id)
        if missing:
            return missing
        args = {"device_id": ctx.device_id}
        if name == "get_device_solar_data":
            args["date"] = ctx.today
        return args, None

    if name in {"get_workout_by_id", "download_workout"}:
        missing = dependency("workout_id", ctx.workout_id)
        if missing:
            return missing
        args = {"workout_id": ctx.workout_id}
        return args, None

    if name == "schedule_workout":
        missing = dependency("workout_id", schedule_workout_id)
        if missing:
            return missing
        return {
            "workout_id": schedule_workout_id,
            "calendar_date": ctx.future_date,
            "confirm": True,
        }, None

    if name == "schedule_week":
        missing = dependency("workout_id", schedule_workout_id)
        if missing:
            return missing
        return {
            "week": [{"date": ctx.future_date, "workout_id": schedule_workout_id}],
            "confirm": True,
        }, None

    if name == "schedule_workouts":
        missing = dependency("workout_id", schedule_workout_id)
        if missing:
            return missing
        return {
            "schedules": [
                {"workout_id": schedule_workout_id, "calendar_date": ctx.future_date}
            ],
            "confirm": True,
        }, None

    if name == "upload_course":
        missing = dependency("gpx_path", ctx.generated_gpx_path)
        if missing:
            return missing
        return {
            "gpx_path": ctx.generated_gpx_path,
            "course_name": "garmin mcp live sweep",
            "confirm": True,
        }, None

    if name == "download_activity_file":
        missing = dependency("activity_id", ctx.activity_id)
        if missing:
            return missing
        return {"activity_id": ctx.activity_id, "format": "fit"}, None

    if name == "log_custom_food":
        if ctx.food_id is None:
            return None, "missing dependency: food_id"
        if ctx.serving_id is None:
            return None, "missing dependency: serving_id"
        return {
            "meal_date": ctx.today,
            "meal_time": "12:00:00",
            "food_id": ctx.food_id,
            "serving_id": ctx.serving_id,
            "confirm": True,
        }, None

    if name == "update_custom_food":
        if ctx.food_id is None:
            return None, "missing dependency: food_id"
        if ctx.serving_id is None:
            return None, "missing dependency: serving_id"
        return {
            "food_id": ctx.food_id,
            "serving_id": ctx.serving_id,
            "food_name": ctx.custom_food_name,
            "calories": 1,
            "confirm": True,
        }, None

    return None, "no argument recipe"


def classify(text: str, tool_name: str | None = None) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    if tool_name in ACCOUNT_LIMITED_TOOLS and (
        "403" in lowered or "forbidden" in lowered
    ):
        return "ACCOUNT_LIMITED"
    if any(marker in lowered for marker in RETRYABLE_UPSTREAM_MARKERS):
        return "UPSTREAM_UNAVAILABLE"
    if stripped.startswith("Error"):
        return "ERROR"
    if stripped.startswith(EXPECTED_NO_DATA_PREFIXES):
        return "NO_DATA"
    parsed = maybe_json(stripped)
    if isinstance(parsed, dict):
        if parsed.get("status") == "failed":
            return "ERROR"
        if parsed.get("status") == "not_found":
            return "NO_DATA"
        failed = parsed.get("failed")
        if isinstance(failed, int) and failed > 0:
            return "ERROR"
        results = parsed.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                if item.get("status") in {"failed", "error"}:
                    return "ERROR"
    return "OK"


def tool_order_key(tool_name: str) -> tuple[int, str]:
    if tool_name in DESTRUCTIVE_TOOLS_REQUIRING_SWEEP_CREATED_IDS:
        return (3, tool_name)
    if tool_name in CREATION_TOOLS_THAT_PRODUCE_CLEANUP_TARGETS:
        return (1, tool_name)
    if tool_name in MUTATING_TOOLS:
        return (2, tool_name)
    return (0, tool_name)


def record_created_artifacts(
    tool_name: str, text: str, ctx: SweepContext, client: Garmin | None = None
) -> None:
    parsed = maybe_json(text.strip())

    if tool_name in {
        "create_run_workout",
        "create_strength_workout",
        "create_walk_run_workout",
        "create_z2_walk_workout",
        "upload_workout",
    } and isinstance(parsed, dict):
        append_unique(ctx.created_workout_ids, parsed.get("workout_id"))
        append_unique(ctx.created_workout_ids, parsed.get("workoutId"))

    if tool_name == "upload_workouts" and isinstance(parsed, dict):
        for result in parsed.get("results", []):
            if isinstance(result, dict) and result.get("status") == "success":
                append_unique(ctx.created_workout_ids, result.get("workout_id"))
                append_unique(ctx.created_workout_ids, result.get("workoutId"))

    if tool_name == "upload_course" and isinstance(parsed, dict):
        append_unique(ctx.created_course_ids, parsed.get("course_id"))
        append_unique(ctx.created_course_ids, parsed.get("courseId"))

    if tool_name == "create_custom_food" and isinstance(parsed, dict):
        meta = parsed.get("foodMetaData", parsed)
        food_id = meta.get("foodId") or parsed.get("foodId")
        if food_id is not None:
            ctx.food_id = str(food_id)
        contents = parsed.get("nutritionContents") or []
        if contents and isinstance(contents[0], dict):
            serving_id = contents[0].get("servingId")
            if serving_id is not None:
                ctx.serving_id = str(serving_id)

    if tool_name in {
        "add_body_composition",
        "add_weigh_in",
        "add_weigh_in_with_timestamps",
    } and not text.strip().startswith("Error"):
        append_unique(ctx.created_weigh_in_dates, ctx.today)

    if tool_name in {"log_food", "log_custom_food", "upsert_and_log"} and client is not None:
        collect_food_log_ids(client, ctx)

    if tool_name == "set_blood_pressure" and isinstance(parsed, dict) and client is not None:
        cleanup_blood_pressure(client, ctx, parsed)


async def sweep_resources(app: FastMCP) -> list[dict[str, Any]]:
    results = []
    for resource in await app.list_resources():
        uri = str(resource.uri)
        try:
            contents = await app.read_resource(resource.uri)
            text = "\n".join(getattr(item, "content", str(item)) for item in contents)
            status = "OK" if maybe_json(text) is not None else "ERROR"
            results.append({"kind": "resource", "name": uri, "status": status})
        except Exception as exc:
            results.append(
                {"kind": "resource", "name": uri, "status": "ERROR", "error": str(exc)}
            )
    return results


async def sweep_tools(
    app: FastMCP,
    ctx: SweepContext,
    include_mutations: bool,
    client: Garmin | None = None,
) -> list[dict[str, Any]]:
    results = []
    tools = sorted(await app.list_tools(), key=lambda tool: tool_order_key(tool.name))
    for tool in tools:
        name = tool.name
        if name in MUTATING_TOOLS and not include_mutations:
            results.append(
                {"kind": "tool", "name": name, "status": "SKIPPED_MUTATION"}
            )
            continue

        call_args, skip_reason = arguments_for(name, ctx)
        if skip_reason:
            results.append(
                {
                    "kind": "tool",
                    "name": name,
                    "status": "SKIPPED_DEPENDENCY",
                    "reason": skip_reason,
                }
            )
            continue

        try:
            content = await app.call_tool(name, call_args or {})
            text = content_to_text(content)
            status = classify(text, name)
            if include_mutations and status in {"OK", "NO_DATA"}:
                record_created_artifacts(name, text, ctx, client)
            results.append(
                {
                    "kind": "tool",
                    "name": name,
                    "status": status,
                    "args": call_args or {},
                    "preview": text[:500],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "kind": "tool",
                    "name": name,
                    "status": "EXCEPTION",
                    "args": call_args or {},
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=4),
                }
            )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1
    return dict(sorted(summary.items()))


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-path", default=get_token_path())
    parser.add_argument("--date", help="YYYY-MM-DD date to use for date-based tools")
    parser.add_argument("--days-back", type=int, default=14)
    parser.add_argument("--include-mutations", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--json-out", help="Optional path to write full JSON results")
    args = parser.parse_args()

    class InventoryClient:
        def __getattr__(self, name: str) -> Any:
            def call(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError(f"inventory client cannot call {name}")

            return call

    if args.inventory_only:
        app = build_app(InventoryClient())
        tools = await app.list_tools()
        resources = await app.list_resources()
        print(json.dumps({"tools": len(tools), "resources": len(resources)}, indent=2))
        return 0

    is_valid, error = validate_tokens(args.token_path)
    if not is_valid:
        print(f"Garmin tokens are not usable: {error}")
        print("Run: uv run garmin-mcp-auth --force-reauth")
        return 2

    client = Garmin()
    client.login(args.token_path)
    ctx = collect_context(client, args)
    app = build_app(client)

    results = await sweep_resources(app)
    results.extend(await sweep_tools(app, ctx, args.include_mutations, client))

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "include_mutations": args.include_mutations,
        "context": ctx.__dict__,
        "summary": summarize(results),
        "results": results,
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps({"summary": payload["summary"], "context_notes": ctx.notes}, indent=2))
    return 1 if any(r["status"] in {"ERROR", "EXCEPTION"} for r in results) else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
