"""Regression coverage for the Garmin metric semantic contract."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import metric_catalog


def test_catalog_covers_the_pinned_registered_tool_surface():
    metric_catalog.validate_catalog_coverage()

    catalog = metric_catalog.build_catalog()
    inventory = metric_catalog.registered_tool_inventory()

    assert catalog["catalog_version"] == metric_catalog.CATALOG_VERSION
    assert catalog["tool_count"] == len(inventory) == metric_catalog.EXPECTED_TOOL_COUNT
    assert catalog["tool_inventory_fingerprint"] == metric_catalog.EXPECTED_TOOL_INVENTORY_FINGERPRINT
    assert {entry["tool"] for entry in catalog["tool_coverage"]} == set(inventory)
    inventory_fields = {
        (entry["tool"], entry["field_key"])
        for entry in metric_catalog.registered_output_field_inventory()
    }
    catalog_fields = {
        (entry["tool"], entry["field_key"])
        for entry in catalog["field_coverage"]
    }
    assert inventory_fields <= catalog_fields
    assert all(entry["status"] in {"curated", "opaque", "metadata"} for entry in catalog["tool_coverage"])
    assert catalog["output_field_count"] == metric_catalog.EXPECTED_OUTPUT_FIELD_COUNT
    assert (
        catalog["output_field_inventory_fingerprint"]
        == metric_catalog.EXPECTED_OUTPUT_FIELD_INVENTORY_FINGERPRINT
    )
    assert len(catalog["field_coverage"]) >= catalog["output_field_count"]
    assert all(
        entry["status"] in {"official", "derived", "opaque", "metadata", "curated"}
        for entry in catalog["field_coverage"]
    )


def test_catalog_metrics_have_required_semantic_metadata():
    required = {
        "metric_id",
        "name",
        "status",
        "official_source_url",
        "verified_on",
        "raw_unit",
        "output_unit",
        "aggregation_window",
        "transformation",
        "tool_field_paths",
        "coaching_eligible",
        "limitations",
    }
    catalog = metric_catalog.build_catalog()

    assert {metric["metric_id"] for metric in catalog["metrics"]} >= {
        "garmin.recovery_time.minutes",
        "garmin.training_effect.aerobic",
        "garmin.training_effect.anaerobic",
        "garmin.exercise_load",
        "garmin.intensity_minutes.weighted_credit",
        "garmin.sleep.nap_duration",
        "garmin.sleep.stage.deep_duration",
        "local.sleep.time_in_bed",
        "garmin.body_battery.wake_level",
        "local.fit.ftp_from_20_minute_power",
        "garmin.mcp.raw_passthrough",
        "garmin.fit.developer_field",
    }
    for metric in catalog["metrics"]:
        assert required <= set(metric)
        assert metric["status"] in {"official", "derived", "opaque", "metadata"}
        assert metric["tool_field_paths"]


def test_newer_source_surface_is_explicitly_fail_closed():
    catalog = metric_catalog.build_catalog()
    coverage = {entry["tool"]: entry["status"] for entry in catalog["tool_coverage"]}

    assert coverage["get_metric_catalog"] == "metadata"
    for tool in {
        "download_course_gpx",
        "get_acclimation",
        "get_activity_fit_messages",
        "get_calendar_events",
        "get_course_details",
        "get_energy_balance",
        "get_heart_rate_zones",
        "get_nutrition_summary_between_dates",
        "get_running_tolerance",
        "get_running_tolerance_trend",
        "get_sleep_summary_range",
        "get_stats_range",
    }:
        assert coverage[tool] == "opaque"

    assert all(
        entry["status"] in {"official", "derived", "opaque", "metadata", "curated"}
        for entry in catalog["field_coverage"]
    )


def test_body_battery_and_stress_fields_fail_closed_at_ambiguous_paths():
    catalog = metric_catalog.build_catalog()
    fields = {
        (entry["tool"], entry["field_key"]): entry
        for entry in catalog["field_coverage"]
    }
    metrics = {metric["metric_id"]: metric for metric in catalog["metrics"]}

    body_battery = metrics["garmin.body_battery.level"]
    assert {
        (path["tool"], path["path"]) for path in body_battery["tool_field_paths"]
    } >= {
        ("get_body_battery", "[].body_battery_level_numeric"),
    }
    assert ("get_body_battery", "[].body_battery_level") not in {
        (path["tool"], path["path"]) for path in body_battery["tool_field_paths"]
    }
    assert "garmin.body_battery.display_category" not in metrics
    for field_key in (
        "body_battery_level",
        "body_battery_level_category",
        "current_feedback",
    ):
        entry = fields[("get_body_battery", field_key)]
        assert entry["status"] == "opaque"
        assert entry["metric_ids"] == []

    morning_stress = fields[("get_morning_training_readiness", "stress_level")]
    assert morning_stress["status"] == "opaque"
    assert morning_stress["metric_ids"] == []

    average_stress = metrics["garmin.stress.average"]
    assert {
        (path["tool"], path["path"]) for path in average_stress["tool_field_paths"]
    } == {
        ("get_stats", "avg_stress_level"),
        ("get_stress_summary", "average_stress_level"),
    }


@pytest.mark.asyncio
async def test_catalog_is_available_as_a_read_only_mcp_tool():
    app = metric_catalog.register_tools(FastMCP("Metric catalog test"))

    result = await app.call_tool("get_metric_catalog", {})
    payload = json.loads(result[0][0].text)

    assert payload["catalog_version"] == metric_catalog.CATALOG_VERSION
    assert payload["opaque_rule"].startswith("Fields absent from metrics")
    assert payload == metric_catalog.build_catalog()


@pytest.mark.asyncio
async def test_catalog_is_available_as_a_read_only_mcp_resource():
    app = metric_catalog.register_tools(FastMCP("Metric catalog resource test"))

    resources = await app.list_resources()
    assert any(str(resource.uri) == "garmin://metric-catalog" for resource in resources)
    result = await app.read_resource("garmin://metric-catalog")
    payload = json.loads(result[0].content)
    tool_result = await app.call_tool("get_metric_catalog", {})
    tool_payload = json.loads(tool_result[0][0].text)

    assert payload["catalog_version"] == metric_catalog.CATALOG_VERSION
    catalog = metric_catalog.build_catalog()
    for key in (
        "catalog_version",
        "tool_count",
        "tool_inventory_fingerprint",
        "output_field_count",
        "output_field_inventory_fingerprint",
    ):
        assert payload[key] == catalog[key]
        assert payload[key] == tool_payload[key]
