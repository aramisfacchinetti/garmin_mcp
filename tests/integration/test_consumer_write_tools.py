"""Integration coverage for confirmation-gated consumer-client writes."""

import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import consumer_writes


@pytest.fixture
def app_with_consumer_writes(mock_garmin_client):
    consumer_writes.configure(mock_garmin_client)
    app = FastMCP("Test Consumer Writes")
    return consumer_writes.register_tools(app)


def tool_data(result):
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_manual_activity_json_requires_confirmation(
    app_with_consumer_writes, mock_garmin_client
):
    payload = {
        "activityName": "Manual walk",
        "activityTypeDTO": {"typeKey": "walking"},
        "summaryDTO": {"duration": 600},
    }

    preview = await app_with_consumer_writes.call_tool(
        "create_manual_activity_from_json", {"payload": payload}
    )
    assert tool_data(preview)["status"] == "confirmation_required"
    mock_garmin_client.create_manual_activity_from_json.assert_not_called()

    mock_garmin_client.create_manual_activity_from_json.return_value = {
        "activityId": 123
    }
    confirmed = await app_with_consumer_writes.call_tool(
        "create_manual_activity_from_json", {"payload": payload, "confirm": True}
    )
    data = tool_data(confirmed)
    assert data["status"] == "ok"
    assert data["response"]["activityId"] == 123
    mock_garmin_client.create_manual_activity_from_json.assert_called_once_with(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["upload_activity", "import_activity"])
async def test_activity_file_writes_require_confirmation(
    app_with_consumer_writes, mock_garmin_client, tmp_path, tool_name
):
    activity_file = tmp_path / "activity.fit"
    activity_file.write_bytes(b"FIT test fixture")

    preview = await app_with_consumer_writes.call_tool(
        tool_name, {"activity_path": str(activity_file)}
    )
    assert tool_data(preview)["status"] == "confirmation_required"
    getattr(mock_garmin_client, tool_name).assert_not_called()

    getattr(mock_garmin_client, tool_name).return_value = {"activityId": 456}
    confirmed = await app_with_consumer_writes.call_tool(
        tool_name, {"activity_path": str(activity_file), "confirm": True}
    )
    assert tool_data(confirmed)["status"] == "ok"
    getattr(mock_garmin_client, tool_name).assert_called_once_with(str(activity_file))


@pytest.mark.asyncio
async def test_activity_file_write_rejects_unsupported_extension(
    app_with_consumer_writes, mock_garmin_client, tmp_path
):
    activity_file = tmp_path / "activity.csv"
    activity_file.write_text("not a supported import")

    result = await app_with_consumer_writes.call_tool(
        "upload_activity",
        {"activity_path": str(activity_file), "confirm": True},
    )

    assert tool_data(result)["status"] == "invalid_argument"
    mock_garmin_client.upload_activity.assert_not_called()


@pytest.mark.asyncio
async def test_exercise_set_write_requires_confirmation_and_verifies(
    app_with_consumer_writes, mock_garmin_client
):
    payload = {"exerciseSets": [{"category": "BENCH_PRESS", "weight": 20}]}

    preview = await app_with_consumer_writes.call_tool(
        "set_activity_exercise_sets",
        {"activity_id": 123, "payload": payload},
    )
    assert tool_data(preview)["status"] == "confirmation_required"
    mock_garmin_client.set_activity_exercise_sets.assert_not_called()

    mock_garmin_client.set_activity_exercise_sets.return_value = None
    mock_garmin_client.get_activity_exercise_sets.return_value = payload
    confirmed = await app_with_consumer_writes.call_tool(
        "set_activity_exercise_sets",
        {"activity_id": 123, "payload": payload, "confirm": True},
    )
    data = tool_data(confirmed)
    assert data["status"] == "ok"
    assert data["postcondition"]["status"] == "verified"
    mock_garmin_client.set_activity_exercise_sets.assert_called_once_with(123, payload)
    mock_garmin_client.get_activity_exercise_sets.assert_called_once_with(123)


@pytest.mark.asyncio
async def test_gear_default_write_requires_confirmation_and_verifies(
    app_with_consumer_writes, mock_garmin_client
):
    mock_garmin_client.get_device_last_used.return_value = {
        "userProfileNumber": "42"
    }
    mock_garmin_client.get_gear_defaults.return_value = [
        {"uuid": "gear-1", "activityTypePk": 1}
    ]

    preview = await app_with_consumer_writes.call_tool(
        "set_gear_default",
        {"activity_type": "1", "gear_uuid": "gear-1"},
    )
    assert tool_data(preview)["status"] == "confirmation_required"
    mock_garmin_client.set_gear_default.assert_not_called()

    confirmed = await app_with_consumer_writes.call_tool(
        "set_gear_default",
        {
            "activity_type": "1",
            "gear_uuid": "gear-1",
            "default_gear": True,
            "confirm": True,
        },
    )
    data = tool_data(confirmed)
    assert data["status"] == "ok"
    assert data["postcondition"]["status"] == "verified"
    mock_garmin_client.set_gear_default.assert_called_once_with(
        "1", "gear-1", default_gear=True
    )
    mock_garmin_client.get_gear_defaults.assert_called_once_with("42")


@pytest.mark.asyncio
async def test_write_reports_unavailable_method_after_confirmation(tmp_path):
    client = Mock(spec=[])
    consumer_writes.configure(client)
    app = consumer_writes.register_tools(FastMCP("Unavailable Consumer Writes"))
    activity_file = tmp_path / "activity.fit"
    activity_file.write_bytes(b"FIT test fixture")

    result = await app.call_tool(
        "upload_activity",
        {"activity_path": str(activity_file), "confirm": True},
    )

    data = tool_data(result)
    assert data["status"] == "unavailable"
    assert "upload_activity" in data["error"]


@pytest.mark.asyncio
async def test_delete_activity_requires_exact_target_confirmation(
    app_with_consumer_writes, mock_garmin_client
):
    mock_garmin_client.get_activity.return_value = {
        "activityName": "Test activity",
        "summaryDTO": {"startTimeLocal": "2024-01-15 07:00:00"},
    }

    preview = await app_with_consumer_writes.call_tool(
        "delete_activity", {"activity_id": 123}
    )
    assert tool_data(preview)["status"] == "confirmation_required"
    mock_garmin_client.delete_activity.assert_not_called()

    mock_garmin_client.delete_activity.return_value = None
    mock_garmin_client.get_activity.side_effect = [
        {"activityName": "Test activity"},
        None,
    ]
    confirmed = await app_with_consumer_writes.call_tool(
        "delete_activity", {"activity_id": 123, "confirm": True}
    )
    assert tool_data(confirmed)["postcondition"]["status"] == "verified"
    mock_garmin_client.delete_activity.assert_called_once_with("123")


@pytest.mark.asyncio
async def test_delete_activity_does_not_treat_verification_errors_as_success(
    app_with_consumer_writes, mock_garmin_client
):
    mock_garmin_client.get_activity.side_effect = [
        {"activityName": "Test activity"},
        RuntimeError("temporary Garmin outage"),
    ]

    result = await app_with_consumer_writes.call_tool(
        "delete_activity", {"activity_id": 123, "confirm": True}
    )

    assert tool_data(result)["postcondition"]["status"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "method_name", "arguments", "preflight_method"),
    [
        (
            "delete_blood_pressure",
            "delete_blood_pressure",
            {"version": "v1", "date": "2024-01-15"},
            "get_blood_pressure",
        ),
        (
            "delete_weigh_in",
            "delete_weigh_in",
            {"weight_pk": "w1", "date": "2024-01-15"},
            "get_daily_weigh_ins",
        ),
    ],
)
async def test_individual_record_deletes_require_confirmation(
    app_with_consumer_writes,
    mock_garmin_client,
    tool_name,
    method_name,
    arguments,
    preflight_method,
):
    getattr(mock_garmin_client, preflight_method).return_value = {}
    preview = await app_with_consumer_writes.call_tool(tool_name, arguments)
    assert tool_data(preview)["status"] == "confirmation_required"
    getattr(mock_garmin_client, method_name).assert_not_called()

    getattr(mock_garmin_client, method_name).return_value = None
    if tool_name == "delete_blood_pressure":
        mock_garmin_client.get_blood_pressure.return_value = {}
    else:
        mock_garmin_client.get_weigh_ins.return_value = {}
    confirmed = await app_with_consumer_writes.call_tool(
        tool_name, {**arguments, "confirm": True}
    )
    assert tool_data(confirmed)["status"] == "ok"
    assert tool_data(confirmed)["postcondition"]["status"] == "verified"


@pytest.mark.asyncio
async def test_record_delete_postcondition_detects_remaining_identifier(
    app_with_consumer_writes, mock_garmin_client
):
    mock_garmin_client.get_blood_pressure.return_value = {
        "measurementSummaries": [{"measurements": [{"version": "v1"}]}]
    }

    result = await app_with_consumer_writes.call_tool(
        "delete_blood_pressure",
        {"version": "v1", "date": "2024-01-15", "confirm": True},
    )

    assert tool_data(result)["postcondition"]["status"] == "unverified"
