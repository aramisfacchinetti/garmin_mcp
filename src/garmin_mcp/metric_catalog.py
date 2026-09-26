"""Versioned semantic contract for every Garmin MCP tool.

The Garmin Connect consumer API is intentionally loosely typed.  This catalog
is the boundary that prevents a response field from being treated as a
coaching fact merely because its name sounds familiar.  Curated fields have a
stable metric id and documented meaning; every other registered tool is
explicitly classified as opaque, metadata, or mutation output.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any


CATALOG_VERSION = "2026.09.1"
VERIFIED_ON = "2026-08-25"
INVENTORY_REFRESHED_ON = "2026-09-26"
# Deliberately pinned to the source surface at this catalog version.  Adding or
# removing a tool requires an explicit catalog review even though the default
# runtime classification remains fail-closed/opaque.
EXPECTED_TOOL_COUNT = 175
EXPECTED_TOOL_INVENTORY_FINGERPRINT = "f8fa4fcdc2a5b27b4668d3e7d3355e42bf80762d0bdd1326e4772bea8ffbce79"
# The field inventory is deliberately pinned too.  It is generated from every
# decorated tool's literal output-map keys; a source change cannot quietly add
# a numerically named field that defaults to a coaching input.
EXPECTED_OUTPUT_FIELD_COUNT = 1234
EXPECTED_OUTPUT_FIELD_INVENTORY_FINGERPRINT = "f17864a21787afcf45b10721e30d7e022a15c7d0a41e1bcd5cfce28a1cf7b1d4"

_SOURCES = {
    "recovery_time": "https://support.garmin.com/en-CA/?faq=8ImmxVkZMh4EYYq5Zp2bR8",
    "training_readiness": "https://support.garmin.com/en-IE/?faq=hsKqNlQksk0Q6Zf1EbIjO9",
    "naps": "https://support.garmin.com/en-US/?faq=2icqqZFq3YAX5K9VGU2CQ9",
    "training_load": "https://support.garmin.com/nl-NL/?faq=SEkNpdGyhR917js0qQL3Q6",
    "body_battery": "https://support.garmin.com/en-SG/?faq=2qczgfbN00AIMJbX33dRq9",
    "hrv": "https://support.garmin.com/en-GB/?faq=HnFAR4oFRF4kHeqYme3bU6",
    "stress": "https://support.garmin.com/en-HK/?faq=WT9BmhjacO4ZpxbCc0EKn9",
    "intensity_minutes": "https://support.garmin.com/en-US/aviation/faq/pNU9nnDzzGAHmEavp9rpY8/",
    "fit": "https://developer.garmin.com/fit/protocol/",
}


def _metric(
    metric_id: str,
    *,
    name: str,
    status: str,
    source: str | None,
    raw_unit: str | None,
    output_unit: str | None,
    aggregation_window: str,
    transformation: str,
    paths: list[dict[str, str]],
    coaching_eligible: bool,
    limitations: str,
) -> dict[str, Any]:
    """Build one catalog record with one uniform machine-readable shape."""

    return {
        "metric_id": metric_id,
        "name": name,
        "status": status,
        "official_source_url": source,
        "verified_on": VERIFIED_ON,
        "raw_unit": raw_unit,
        "output_unit": output_unit,
        "aggregation_window": aggregation_window,
        "transformation": transformation,
        "tool_field_paths": paths,
        "coaching_eligible": coaching_eligible,
        "limitations": limitations,
    }


# This is deliberately a conservative allowlist.  A field that is not here is
# not silently promoted because a new Garmin firmware/API response happens to
# use a familiar label.
METRICS: tuple[dict[str, Any], ...] = (
    _metric(
        "garmin.training_readiness.score",
        name="Training Readiness score",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="score",
        output_unit="score",
        aggregation_window="current continuously-updated assessment",
        transformation="passthrough",
        paths=[
            {"tool": "get_training_readiness", "path": "[].score"},
            {"tool": "get_morning_training_readiness", "path": "readiness_score"},
        ],
        coaching_eligible=True,
        limitations="A readiness assessment, not a performance forecast or automatic workout authorization.",
    ),
    _metric(
        "garmin.training_readiness.level",
        name="Training Readiness display level",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="category",
        output_unit="category",
        aggregation_window="current continuously-updated assessment",
        transformation="passthrough",
        paths=[
            {"tool": "get_training_readiness", "path": "[].level"},
            {"tool": "get_morning_training_readiness", "path": "readiness_level"},
        ],
        coaching_eligible=True,
        limitations="A categorical companion to the score; it does not override safety, symptoms, or recovery constraints.",
    ),
    _metric(
        "garmin.recovery_time.minutes",
        name="Recovery Time",
        status="official",
        source=_SOURCES["recovery_time"],
        raw_unit="min",
        output_unit="h",
        aggregation_window="current post-activity estimate",
        transformation="canonical hours = raw minutes / 60, rounded only for display",
        paths=[
            {"tool": "get_training_readiness", "path": "[].recovery_time_minutes"},
            {"tool": "get_morning_training_readiness", "path": "recovery_time_minutes"},
            {"tool": "get_training_effect", "path": "recovery_time_minutes"},
            {"tool": "get_activity", "path": "recovery_time_minutes"},
        ],
        coaching_eligible=True,
        limitations="Means time until another workout of similar intensity; it is not a universal rest prescription.",
    ),
    _metric(
        "garmin.exercise_load",
        name="Exercise Load",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="EPOC-based load score",
        output_unit="EPOC-based load score",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[
            {"tool": "get_activity", "path": "exercise_load"},
            {"tool": "get_training_effect", "path": "exercise_load"},
        ],
        coaching_eligible=True,
        limitations="Per-activity EPOC-based load; do not equate it with Training Effect or reconstruct Garmin acute load arithmetically.",
    ),
    _metric(
        "garmin.activity.duration",
        name="Activity duration",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="s",
        output_unit="min",
        aggregation_window="one activity",
        transformation="seconds / 60",
        paths=[{"tool": "get_activity", "path": "duration_seconds"}],
        coaching_eligible=True,
        limitations="Keep timer, moving, and elapsed duration distinct when the source exposes more than one basis.",
    ),
    _metric(
        "garmin.activity.distance",
        name="Activity distance",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="m",
        output_unit="km",
        aggregation_window="one activity",
        transformation="meters / 1000",
        paths=[{"tool": "get_activity", "path": "distance_meters"}],
        coaching_eligible=True,
        limitations="Recorded distance; it can be a lower bound when the original activity has an auto-stop or recording-quality limitation.",
    ),
    _metric(
        "garmin.activity.ascent",
        name="Activity elevation gain",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="m",
        output_unit="m",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[{"tool": "get_activity", "path": "elevation_gain_meters"}],
        coaching_eligible=True,
        limitations="Recorded elevation gain; device and elevation-correction behavior can affect comparability.",
    ),
    _metric(
        "garmin.activity.descent",
        name="Activity elevation loss",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="m",
        output_unit="m",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[{"tool": "get_activity", "path": "elevation_loss_meters"}],
        coaching_eligible=True,
        limitations="Recorded elevation loss; device and elevation-correction behavior can affect comparability.",
    ),
    _metric(
        "garmin.activity.average_heart_rate",
        name="Activity average heart rate",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="bpm",
        output_unit="bpm",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[{"tool": "get_activity", "path": "avg_hr_bpm"}],
        coaching_eligible=True,
        limitations="Sensor quality and sport context must accompany any zone or threshold interpretation.",
    ),
    _metric(
        "garmin.activity.maximum_heart_rate",
        name="Activity maximum heart rate",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="bpm",
        output_unit="bpm",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[{"tool": "get_activity", "path": "max_hr_bpm"}],
        coaching_eligible=True,
        limitations="A measured maximum, not a threshold estimate or an automatically valid zone ceiling.",
    ),
    _metric(
        "garmin.activity.body_battery_impact",
        name="Activity Body Battery impact",
        status="official",
        source=_SOURCES["body_battery"],
        raw_unit="Body Battery points",
        output_unit="Body Battery points",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[{"tool": "get_activity", "path": "body_battery_impact"}],
        coaching_eligible=True,
        limitations="A Body Battery change attributed in the activity summary; it is not an independent exercise-load scale.",
    ),
    _metric(
        "garmin.training_load.acute",
        name="Acute Training Load",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="weighted load score",
        output_unit="weighted load score",
        aggregation_window="Garmin weighted trailing seven days",
        transformation="passthrough from Garmin acute-load DTO",
        paths=[
            {"tool": "get_training_load_trend", "path": "trend[].garmin_acute_load"},
            {"tool": "get_training_readiness", "path": "[].acute_load"},
            {"tool": "get_morning_training_readiness", "path": "acute_load"},
        ],
        coaching_eligible=True,
        limitations="Use the Garmin value as supplied; summing activity loads is not equivalent to Garmin's weighting.",
    ),
    _metric(
        "garmin.training_load.chronic",
        name="Chronic Training Load",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="weighted load score",
        output_unit="weighted load score",
        aggregation_window="Garmin weighted 28-day acute-load history",
        transformation="passthrough from Garmin acute-load DTO",
        paths=[
            {"tool": "get_training_load_trend", "path": "trend[].garmin_chronic_load"},
            {"tool": "get_morning_training_readiness", "path": "chronic_load"},
        ],
        coaching_eligible=True,
        limitations="Not interchangeable with third-party CTL formulas.",
    ),
    _metric(
        "garmin.training_load.ratio",
        name="Acute-to-chronic load ratio",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="ratio",
        output_unit="ratio",
        aggregation_window="current Garmin acute divided by chronic load",
        transformation="passthrough from Garmin ratio; never recompute from rounded display values",
        paths=[
            {"tool": "get_training_load_trend", "path": "trend[].garmin_acute_chronic_load_ratio"},
        ],
        coaching_eligible=True,
        limitations="Only valid when supplied by the same Garmin training-status record as its acute and chronic values.",
    ),
    _metric(
        "garmin.training_status",
        name="Training Status",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="category",
        output_unit="category",
        aggregation_window="current Garmin training-status assessment",
        transformation="passthrough",
        paths=[
            {"tool": "get_training_status", "path": "training_status"},
            {"tool": "get_training_load_trend", "path": "trend[].training_status"},
        ],
        coaching_eligible=True,
        limitations="A Garmin assessment category, not a medical diagnosis or an automatic session authorization.",
    ),
    _metric(
        "garmin.resting_heart_rate",
        name="Resting heart rate",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="bpm",
        output_unit="bpm",
        aggregation_window="Garmin daily resting-heart-rate summary",
        transformation="passthrough",
        paths=[
            {"tool": "get_stats", "path": "resting_heart_rate_bpm"},
            {"tool": "get_heart_rates_summary", "path": "resting_heart_rate_bpm"},
        ],
        coaching_eligible=True,
        limitations="Compare with the athlete's own context and data quality; it is not an automatic illness diagnosis.",
    ),
    _metric(
        "garmin.resting_heart_rate.seven_day_average",
        name="Seven-day average resting heart rate",
        status="official",
        source=_SOURCES["fit"],
        raw_unit="bpm",
        output_unit="bpm",
        aggregation_window="Garmin trailing seven days",
        transformation="passthrough",
        paths=[{"tool": "get_stats", "path": "last_7_days_avg_resting_hr"}],
        coaching_eligible=True,
        limitations="Distinct from the current resting-heart-rate value.",
    ),
    _metric(
        "garmin.vo2_max",
        name="VO2 Max estimate",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="ml/kg/min",
        output_unit="ml/kg/min",
        aggregation_window="latest Garmin estimate by sport",
        transformation="passthrough with sport context",
        paths=[
            {"tool": "get_training_status", "path": "vo2_max"},
            {"tool": "get_vo2max_trend", "path": "trend[].vo2_max"},
        ],
        coaching_eligible=True,
        limitations="Sport-specific estimate; do not mix running and cycling values or treat it as a race-performance guarantee.",
    ),
    _metric(
        "garmin.training_effect.aerobic",
        name="Aerobic Training Effect",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="0-5 score",
        output_unit="0-5 score",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[
            {"tool": "get_activity", "path": "aerobic_training_effect"},
            {"tool": "get_training_effect", "path": "aerobic_training_effect"},
        ],
        coaching_eligible=True,
        limitations="Physiological impact score, not a session class, duration target, or exercise-load value.",
    ),
    _metric(
        "garmin.training_effect.anaerobic",
        name="Anaerobic Training Effect",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="0-5 score",
        output_unit="0-5 score",
        aggregation_window="one activity",
        transformation="passthrough",
        paths=[
            {"tool": "get_activity", "path": "anaerobic_training_effect"},
            {"tool": "get_training_effect", "path": "anaerobic_training_effect"},
        ],
        coaching_eligible=True,
        limitations="Separate from aerobic Training Effect and not a framework quality classification.",
    ),
    _metric(
        "garmin.hrv.last_night_average",
        name="Last-night average HRV",
        status="official",
        source=_SOURCES["hrv"],
        raw_unit="ms",
        output_unit="ms",
        aggregation_window="last sleep period",
        transformation="passthrough",
        paths=[
            {"tool": "get_hrv_data", "path": "last_night_avg_hrv_ms"},
            {"tool": "get_morning_training_readiness", "path": "hrv_last_night"},
        ],
        coaching_eligible=True,
        limitations="Interpret only against the athlete's Garmin personal baseline and status.",
    ),
    _metric(
        "garmin.hrv.last_night_five_minute_high",
        name="Last-night five-minute high HRV",
        status="official",
        source=_SOURCES["hrv"],
        raw_unit="ms",
        output_unit="ms",
        aggregation_window="highest five-minute window of last sleep period",
        transformation="passthrough",
        paths=[{"tool": "get_hrv_data", "path": "last_night_5min_high_hrv_ms"}],
        coaching_eligible=True,
        limitations="A distinct high-window statistic; never substitute it for last-night average or weekly average.",
    ),
    _metric(
        "garmin.hrv.weekly_average",
        name="Weekly average HRV",
        status="official",
        source=_SOURCES["hrv"],
        raw_unit="ms",
        output_unit="ms",
        aggregation_window="Garmin rolling weekly window",
        transformation="passthrough",
        paths=[
            {"tool": "get_hrv_data", "path": "weekly_avg_hrv_ms"},
            {"tool": "get_training_readiness", "path": "[].hrv_weekly_avg"},
        ],
        coaching_eligible=True,
        limitations="A baseline-relative trend input, not a universal threshold.",
    ),
    _metric(
        "garmin.hrv.status",
        name="HRV Status",
        status="official",
        source=_SOURCES["hrv"],
        raw_unit="category",
        output_unit="category",
        aggregation_window="Garmin personal-baseline assessment",
        transformation="passthrough",
        paths=[
            {"tool": "get_hrv_data", "path": "status"},
            {"tool": "get_morning_training_readiness", "path": "hrv_status"},
        ],
        coaching_eligible=True,
        limitations="Status is personalized to the Garmin baseline; do not convert a raw millisecond value into a universal status.",
    ),
    _metric(
        "garmin.sleep.duration",
        name="Sleep duration",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="seconds / 3600 when raw sleep data is used",
        paths=[{"tool": "get_sleep_summary", "path": "sleep_seconds"}],
        coaching_eligible=True,
        limitations="Sleep duration remains distinct from time in bed, naps, stages, and sleep score.",
    ),
    _metric(
        "garmin.sleep.nap_duration",
        name="Nap duration",
        status="official",
        source=_SOURCES["naps"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one calendar day",
        transformation="seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "nap_seconds"}],
        coaching_eligible=True,
        limitations="A nap is separate from the primary sleep duration, time in bed, sleep score, and sleep stages.",
    ),
    _metric(
        "garmin.sleep.stage.deep_duration",
        name="Deep sleep stage duration",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "deep_sleep_seconds"}],
        coaching_eligible=True,
        limitations="A stage duration is not sleep duration, sleep score, a nap, or time in bed.",
    ),
    _metric(
        "garmin.sleep.stage.light_duration",
        name="Light sleep stage duration",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "light_sleep_seconds"}],
        coaching_eligible=True,
        limitations="A stage duration is not sleep duration, sleep score, a nap, or time in bed.",
    ),
    _metric(
        "garmin.sleep.stage.rem_duration",
        name="REM sleep stage duration",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "rem_sleep_seconds"}],
        coaching_eligible=True,
        limitations="A stage duration is not sleep duration, sleep score, a nap, or time in bed.",
    ),
    _metric(
        "garmin.sleep.stage.awake_duration",
        name="Awake duration during sleep period",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "awake_seconds"}],
        coaching_eligible=True,
        limitations="Awake duration is not sleep duration, sleep score, a nap, or time in bed.",
    ),
    _metric(
        "local.sleep.time_in_bed",
        name="Local time in bed",
        status="derived",
        source=_SOURCES["training_readiness"],
        raw_unit="s",
        output_unit="h",
        aggregation_window="one sleep period",
        transformation="local seconds = (Garmin sleep_end timestamp - sleep_start timestamp) / 1000; canonical hours = local seconds / 3600",
        paths=[{"tool": "get_sleep_summary", "path": "local_time_in_bed_seconds"}],
        coaching_eligible=True,
        limitations="Locally derived from Garmin timestamps; it is not Garmin sleep duration and may include awake time.",
    ),
    _metric(
        "garmin.sleep.score",
        name="Sleep score",
        status="official",
        source=_SOURCES["training_readiness"],
        raw_unit="score",
        output_unit="score",
        aggregation_window="one sleep period",
        transformation="passthrough",
        paths=[
            {"tool": "get_sleep_summary", "path": "sleep_score"},
            {"tool": "get_training_readiness", "path": "[].sleep_score"},
        ],
        coaching_eligible=True,
        limitations="Not interchangeable with duration, stage durations, nap duration, or time in bed.",
    ),
    _metric(
        "garmin.body_battery.level",
        name="Body Battery numeric level",
        status="official",
        source=_SOURCES["body_battery"],
        raw_unit="0-100 level",
        output_unit="0-100 level",
        aggregation_window="current or named event/timepoint",
        transformation="passthrough",
        paths=[
            {"tool": "get_stats", "path": "body_battery_current"},
            {"tool": "get_body_battery", "path": "[].body_battery_level_numeric"},
            {"tool": "get_morning_training_readiness", "path": "body_battery_percent"},
        ],
        coaching_eligible=True,
        limitations="Numeric level must not be mixed with Garmin's categorical display text.",
    ),
    _metric(
        "garmin.body_battery.wake_level",
        name="Body Battery level at wake",
        status="official",
        source=_SOURCES["body_battery"],
        raw_unit="0-100 level",
        output_unit="0-100 level",
        aggregation_window="post-wake morning assessment",
        transformation="passthrough only when Garmin input context is AFTER_WAKEUP_RESET",
        paths=[{"tool": "get_morning_training_readiness", "path": "body_battery_at_wake"}],
        coaching_eligible=True,
        limitations="A post-wake level is a distinct timepoint from current level and daily charged/drained values.",
    ),
    _metric(
        "garmin.body_battery.charged",
        name="Body Battery charged",
        status="official",
        source=_SOURCES["body_battery"],
        raw_unit="Body Battery points",
        output_unit="Body Battery points",
        aggregation_window="one calendar day",
        transformation="passthrough",
        paths=[
            {"tool": "get_stats", "path": "body_battery_charged"},
            {"tool": "get_body_battery", "path": "[].charged"},
        ],
        coaching_eligible=True,
        limitations="A daily charge amount, not the current level or wake level.",
    ),
    _metric(
        "garmin.body_battery.drained",
        name="Body Battery drained",
        status="official",
        source=_SOURCES["body_battery"],
        raw_unit="Body Battery points",
        output_unit="Body Battery points",
        aggregation_window="one calendar day",
        transformation="passthrough",
        paths=[
            {"tool": "get_stats", "path": "body_battery_drained"},
            {"tool": "get_body_battery", "path": "[].drained"},
        ],
        coaching_eligible=True,
        limitations="A daily drain amount, not the current level or Garmin display category.",
    ),
    _metric(
        "garmin.stress.average",
        name="Average Stress",
        status="official",
        source=_SOURCES["stress"],
        raw_unit="0-100 score",
        output_unit="0-100 score",
        aggregation_window="Garmin summary period",
        transformation="passthrough",
        paths=[
            {"tool": "get_stats", "path": "avg_stress_level"},
            {"tool": "get_stress_summary", "path": "average_stress_level"},
        ],
        coaching_eligible=True,
        limitations="HRV-based physiological estimate with possible unmeasurable periods; not a psychological diagnosis.",
    ),
    _metric(
        "garmin.intensity_minutes.moderate",
        name="Moderate Intensity Minutes",
        status="official",
        source=_SOURCES["intensity_minutes"],
        raw_unit="min",
        output_unit="min",
        aggregation_window="Garmin calendar week",
        transformation="passthrough",
        paths=[
            {"tool": "get_weekly_intensity_minutes", "path": "weekly_data[].moderate_minutes"},
            {"tool": "get_activity", "path": "moderate_intensity_minutes"},
        ],
        coaching_eligible=True,
        limitations="Retain separately from vigorous minutes and weighted credit.",
    ),
    _metric(
        "garmin.intensity_minutes.vigorous",
        name="Vigorous Intensity Minutes",
        status="official",
        source=_SOURCES["intensity_minutes"],
        raw_unit="min",
        output_unit="min",
        aggregation_window="Garmin calendar week",
        transformation="passthrough",
        paths=[
            {"tool": "get_weekly_intensity_minutes", "path": "weekly_data[].vigorous_minutes"},
            {"tool": "get_activity", "path": "vigorous_intensity_minutes"},
        ],
        coaching_eligible=True,
        limitations="Retain separately from moderate minutes and weighted credit.",
    ),
    _metric(
        "garmin.intensity_minutes.weighted_credit",
        name="Weighted Intensity Minutes credit",
        status="official",
        source=_SOURCES["intensity_minutes"],
        raw_unit="min credit",
        output_unit="min credit",
        aggregation_window="Garmin calendar week",
        transformation="moderate_minutes + 2 * vigorous_minutes",
        paths=[{"tool": "get_weekly_intensity_minutes", "path": "weekly_data[].weighted_minutes"}],
        coaching_eligible=True,
        limitations="A derived display credit; raw moderate and vigorous values remain the source facts.",
    ),
    _metric(
        "garmin.lactate_threshold.heart_rate",
        name="Lactate Threshold heart rate",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="bpm",
        output_unit="bpm",
        aggregation_window="latest Garmin threshold estimate",
        transformation="passthrough with sport, date, stale flag, and method required",
        paths=[{"tool": "get_lactate_threshold", "path": "lactate_threshold_heart_rate_bpm"}],
        coaching_eligible=True,
        limitations="Cannot support a prescription unless sport, source date, stale flag, and source method are all present.",
    ),
    _metric(
        "garmin.ftp",
        name="Garmin Functional Threshold Power",
        status="official",
        source=_SOURCES["training_load"],
        raw_unit="W",
        output_unit="W",
        aggregation_window="latest Garmin FTP estimate",
        transformation="passthrough with sport, date, stale flag, and method required",
        paths=[
            {"tool": "get_cycling_ftp", "path": "functional_threshold_power_watts"},
            {"tool": "get_lactate_threshold", "path": "functional_threshold_power_watts"},
        ],
        coaching_eligible=True,
        limitations="Not interchangeable with a local 95-percent-of-20-minute estimate.",
    ),
    _metric(
        "local.fit.training_stress_balance",
        name="Local Training Stress Balance",
        status="derived",
        source=_SOURCES["training_load"],
        raw_unit="weighted load score",
        output_unit="weighted load score",
        aggregation_window="one sampled training-status day",
        transformation="Garmin chronic load minus Garmin acute load",
        paths=[{"tool": "get_training_load_trend", "path": "trend[].local_training_stress_balance"}],
        coaching_eligible=True,
        limitations="MCP-derived diagnostic; it is not a Garmin-provided TSB or a substitute for Garmin training status.",
    ),
    _metric(
        "local.fit.ftp_from_20_minute_power",
        name="Local FTP estimate from 20-minute power",
        status="derived",
        source=_SOURCES["fit"],
        raw_unit="W",
        output_unit="W",
        aggregation_window="best captured 20-minute power in requested period",
        transformation="0.95 * best 20-minute mean power",
        paths=[{"tool": "get_power_duration_curve", "path": "local_ftp_estimate_from_20min_w"}],
        coaching_eligible=False,
        limitations="Local heuristic, not Garmin FTP and not a prescription threshold without separate validation.",
    ),
    _metric(
        "local.fit.variability_index",
        name="Variability Index",
        status="derived",
        source=_SOURCES["fit"],
        raw_unit="ratio",
        output_unit="ratio",
        aggregation_window="one FIT session or lap",
        transformation="normalized power / average power",
        paths=[{"tool": "get_activity_fit_data", "path": "session.variability_index"}],
        coaching_eligible=True,
        limitations="Computed locally from FIT records; only valid when both source power values are present.",
    ),
    _metric(
        "local.fit.hr_drift",
        name="Heart-rate drift",
        status="derived",
        source=_SOURCES["fit"],
        raw_unit="percent",
        output_unit="percent",
        aggregation_window="first versus second half of a FIT activity",
        transformation="local ratio comparison of heart rate and power",
        paths=[{"tool": "get_activity_fit_data", "path": "session.hr_drift.hr_drift_pct"}],
        coaching_eligible=True,
        limitations="Local diagnostic; not a Garmin physiological classification or standalone readiness rule.",
    ),
    _metric(
        "local.fit.climb_detection",
        name="FIT climb detection",
        status="derived",
        source=_SOURCES["fit"],
        raw_unit="mixed",
        output_unit="mixed",
        aggregation_window="one FIT activity",
        transformation="local grade, duration, and elevation-gain segmentation",
        paths=[{"tool": "get_activity_fit_data", "path": "climbs[]"}],
        coaching_eligible=True,
        limitations="Local segmentation heuristic; not a Garmin ClimbPro or official route classification.",
    ),
    _metric(
        "local.fit.shift_quality",
        name="FIT electronic-shift quality",
        status="derived",
        source=_SOURCES["fit"],
        raw_unit="category",
        output_unit="category",
        aggregation_window="one shift event or FIT activity",
        transformation="local cadence/terrain rule around DI2 or eTap event",
        paths=[{"tool": "get_activity_fit_data", "path": "shifts[].quality"}],
        coaching_eligible=False,
        limitations="Local descriptive label, not a Garmin performance or technical-skill rating.",
    ),
    _metric(
        "garmin.mcp.raw_passthrough",
        name="Uncurated Garmin MCP payload",
        status="opaque",
        source=None,
        raw_unit=None,
        output_unit=None,
        aggregation_window="tool response",
        transformation="raw passthrough only",
        paths=[{"tool": "get_sleep_data", "path": "$"}],
        coaching_eligible=False,
        limitations="Raw JSON has no verified field-level semantics and must never enter coaching logic until each field is cataloged.",
    ),
    _metric(
        "garmin.fit.developer_field",
        name="FIT developer field",
        status="opaque",
        source=_SOURCES["fit"],
        raw_unit=None,
        output_unit=None,
        aggregation_window="device/application defined",
        transformation="raw passthrough only",
        paths=[{"tool": "get_activity_fit_data", "path": "developer_fields[]"}],
        coaching_eligible=False,
        limitations="Unknown developer fields must never enter coaching logic until cataloged with a documented producer and unit.",
    ),
)


# Tools with curated output fields above.  All unlisted registered tools are
# covered too, but only as opaque payloads.  This keeps future MCP expansion
# fail-closed instead of creating accidental coaching inputs.
_CURATED_TOOLS = {
    path["tool"]
    for metric in METRICS
    for path in metric["tool_field_paths"]
}
_METADATA_PREFIXES = (
    "set_",
    "delete_",
    "create_",
    "update_",
    "upload_",
    "schedule_",
    "request_",
    "log_",
)


def _tool_name(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """Return a FastMCP registered name for a decorated function, if any."""

    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != "tool":
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    return keyword.value.value
        return node.name
    return None


def registered_tool_inventory(source_root: Path | None = None) -> list[str]:
    """Discover the declared tool surface without importing auth-dependent code."""

    source_root = source_root or Path(__file__).resolve().parent
    names: set[str] = set()
    for path in sorted(source_root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:  # pragma: no cover - packaging failure
            raise RuntimeError(f"cannot parse MCP tool source {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                name = _tool_name(node)
                if name:
                    names.add(name)
    return sorted(names)


def tool_inventory_fingerprint(tool_names: list[str] | None = None) -> str:
    """Return a stable source-surface fingerprint for registry pinning/tests."""

    tool_names = tool_names if tool_names is not None else registered_tool_inventory()
    payload = "\n".join(sorted(tool_names)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _literal_output_keys(function: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    """Return literal keys emitted by one tool's curated response maps.

    This is intentionally conservative: it records every literal mapping key
    and subscript-assignment key inside a registered tool.  Some will be
    implementation metadata rather than a metric, but those are explicitly
    classified as ``metadata`` or ``opaque`` below.  Missing a source key is
    riskier than over-classifying one as non-coaching output.
    """

    keys: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
        elif isinstance(node, ast.Subscript):
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                keys.add(slice_node.value)
    return keys


def registered_output_field_inventory(source_root: Path | None = None) -> list[dict[str, str]]:
    """Source-scan all registered output-map keys for fail-closed coverage.

    The inventory complements the exact nested paths in ``METRICS``.  A key
    absent from a curated metric is returned as an explicit opaque/metadata
    field record rather than an untyped value that a consumer may interpret.
    """

    source_root = source_root or Path(__file__).resolve().parent
    entries: set[tuple[str, str]] = set()
    for path in sorted(source_root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:  # pragma: no cover - packaging failure
            raise RuntimeError(f"cannot parse MCP tool source {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            name = _tool_name(node)
            if not name:
                continue
            entries.update((name, key) for key in _literal_output_keys(node))
    return [
        {"tool": tool, "field_key": field_key}
        for tool, field_key in sorted(entries)
    ]


def output_field_inventory_fingerprint(
    fields: list[dict[str, str]] | None = None,
) -> str:
    """Return a stable fingerprint for the source-emitted field surface."""

    fields = fields if fields is not None else registered_output_field_inventory()
    payload = "\n".join(
        f"{entry['tool']}\t{entry['field_key']}" for entry in fields
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _metric_ids_by_field_key() -> dict[tuple[str, str], list[str]]:
    """Map exact catalog paths to their terminal source field key."""

    mapped: dict[tuple[str, str], list[str]] = {}
    for metric in METRICS:
        for path in metric["tool_field_paths"]:
            field_key = path["path"].replace("[]", "").rsplit(".", 1)[-1]
            mapped.setdefault((path["tool"], field_key), []).append(metric["metric_id"])
    return mapped


def _field_coverage_status(tool_name: str, field_key: str) -> dict[str, Any]:
    metric_ids = _metric_ids_by_field_key().get((tool_name, field_key), [])
    if metric_ids:
        records = {
            metric["metric_id"]: metric
            for metric in METRICS
            if metric["metric_id"] in metric_ids
        }
        statuses = {records[metric_id]["status"] for metric_id in metric_ids}
        return {
            "status": statuses.pop() if len(statuses) == 1 else "curated",
            "metric_ids": sorted(metric_ids),
            "coaching_default": "eligible only through the listed metric IDs",
        }
    if _coverage_status(tool_name) == "metadata":
        return {
            "status": "metadata",
            "metric_ids": [],
            "coaching_default": "not a coaching input",
        }
    return {
        "status": "opaque",
        "metric_ids": [],
        "coaching_default": "not a coaching input until an exact metric path is cataloged",
    }


def validate_catalog_coverage() -> None:
    """Fail when source tools change without an intentional catalog update."""

    tools = registered_tool_inventory()
    fingerprint = tool_inventory_fingerprint(tools)
    if len(tools) != EXPECTED_TOOL_COUNT:
        raise ValueError(
            f"catalog {CATALOG_VERSION} expects {EXPECTED_TOOL_COUNT} tools, found {len(tools)}"
        )
    if fingerprint != EXPECTED_TOOL_INVENTORY_FINGERPRINT:
        raise ValueError(
            f"catalog {CATALOG_VERSION} tool inventory changed: {fingerprint}"
        )
    fields = registered_output_field_inventory()
    field_fingerprint = output_field_inventory_fingerprint(fields)
    if len(fields) != EXPECTED_OUTPUT_FIELD_COUNT:
        raise ValueError(
            f"catalog {CATALOG_VERSION} expects {EXPECTED_OUTPUT_FIELD_COUNT} output fields, found {len(fields)}"
        )
    if field_fingerprint != EXPECTED_OUTPUT_FIELD_INVENTORY_FINGERPRINT:
        raise ValueError(
            f"catalog {CATALOG_VERSION} output field inventory changed: {field_fingerprint}"
        )
    known = set(tools)
    for metric in METRICS:
        for path in metric["tool_field_paths"]:
            if path["tool"] not in known:
                raise ValueError(
                    f"metric {metric['metric_id']} names unknown tool {path['tool']}"
                )


def _coverage_status(tool_name: str) -> str:
    if tool_name in _CURATED_TOOLS:
        return "curated"
    if tool_name == "get_metric_catalog":
        return "metadata"
    if tool_name.startswith(_METADATA_PREFIXES):
        return "metadata"
    return "opaque"


def build_catalog() -> dict[str, Any]:
    """Return the catalog sent by the read-only MCP endpoint."""

    tools = registered_tool_inventory()
    source_fields = registered_output_field_inventory()
    field_coverage = [
        {
            **entry,
            **_field_coverage_status(entry["tool"], entry["field_key"]),
        }
        for entry in source_fields
    ]
    # Cataloged nested paths can be produced indirectly and therefore do not
    # always appear as a literal key in the source scanner.  Retain a field
    # coverage record for them as well.
    present = {(entry["tool"], entry["field_key"]) for entry in field_coverage}
    for (tool, field_key), metric_ids in sorted(_metric_ids_by_field_key().items()):
        if (tool, field_key) not in present:
            field_coverage.append(
                {
                    "tool": tool,
                    "field_key": field_key,
                    **_field_coverage_status(tool, field_key),
                }
            )
    return {
        "catalog_version": CATALOG_VERSION,
        "verified_on": VERIFIED_ON,
        "inventory_refreshed_on": INVENTORY_REFRESHED_ON,
        "tool_inventory_fingerprint": tool_inventory_fingerprint(tools),
        "tool_count": len(tools),
        "output_field_count": len(source_fields),
        "output_field_inventory_fingerprint": output_field_inventory_fingerprint(source_fields),
        "tool_coverage": [
            {
                "tool": tool,
                "status": _coverage_status(tool),
                "coaching_default": "eligible only through a cataloged metric field"
                if _coverage_status(tool) == "curated"
                else "not a coaching input",
            }
            for tool in tools
        ],
        "metrics": list(METRICS),
        "field_coverage": sorted(field_coverage, key=lambda entry: (entry["tool"], entry["field_key"])),
        "opaque_rule": (
            "Fields absent from metrics are opaque and cannot be promoted to coaching inputs "
            "without a catalog entry and a new catalog version."
        ),
    }


def catalog_json() -> str:
    """Serialize the exact public contract deterministically."""

    return json.dumps(build_catalog(), indent=2, sort_keys=True)


def register_tools(app):
    """Register the read-only catalog endpoint and resource with a FastMCP app."""

    @app.resource("garmin://metric-catalog")
    def metric_catalog_resource() -> str:
        """Read the versioned Garmin metric semantic contract."""

        return catalog_json()

    @app.tool()
    async def get_metric_catalog() -> str:
        """Return the versioned Garmin metric semantic contract.

        The response covers every currently registered MCP tool.  Only fields
        with a catalog metric ID may be used as a coaching input; raw JSON,
        FIT developer fields, and unlisted output remain opaque.
        """

        return catalog_json()

    return app
