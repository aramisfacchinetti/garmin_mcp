"""Integration coverage for bounded upstream consumer-client read parity."""

import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import consumer_parity


@pytest.fixture
def app_with_consumer_parity(mock_garmin_client):
    consumer_parity.configure(mock_garmin_client)
    app = FastMCP("Test Consumer Parity")
    return consumer_parity.register_tools(app)


def tool_data(result):
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_get_activity_details_is_bounded_and_preserves_raw_data(
    app_with_consumer_parity, mock_garmin_client
):
    mock_garmin_client.get_activity_details.return_value = {
        "activityDetailMetrics": [{"metric": "heartRate"}],
        "activityDetailDTO": {"activityId": 123},
    }

    result = await app_with_consumer_parity.call_tool(
        "get_activity_details",
        {"activity_id": 123, "max_chart": 250, "max_polyline": 750},
    )

    data = tool_data(result)
    assert data["status"] == "ok"
    assert data["limits"] == {"max_chart": 250, "max_polyline": 750}
    assert data["data"]["activityDetailDTO"]["activityId"] == 123
    mock_garmin_client.get_activity_details.assert_called_once_with(
        "123", maxchart=250, maxpoly=750
    )


@pytest.mark.asyncio
async def test_get_activity_details_rejects_unbounded_limits(
    app_with_consumer_parity, mock_garmin_client
):
    result = await app_with_consumer_parity.call_tool(
        "get_activity_details",
        {"activity_id": 123, "max_chart": 2001},
    )

    data = tool_data(result)
    assert data["status"] == "invalid_argument"
    mock_garmin_client.get_activity_details.assert_not_called()


@pytest.mark.asyncio
async def test_gear_and_golf_tools_normalize_and_bound_inputs(
    app_with_consumer_parity, mock_garmin_client
):
    mock_garmin_client.get_gear_activities.return_value = [{"activityId": 1}]
    mock_garmin_client.get_golf_shot_data.return_value = {"shots": []}

    gear_result = await app_with_consumer_parity.call_tool(
        "get_gear_activities", {"gear_uuid": "gear-1", "limit": 20}
    )
    golf_result = await app_with_consumer_parity.call_tool(
        "get_golf_shot_data",
        {"scorecard_id": 99, "hole_numbers": "1, 2, 2, 18"},
    )

    assert tool_data(gear_result)["status"] == "ok"
    assert tool_data(golf_result)["hole_numbers"] == "1,2,18"
    mock_garmin_client.get_gear_activities.assert_called_once_with(
        "gear-1", limit=20
    )
    mock_garmin_client.get_golf_shot_data.assert_called_once_with(
        99, hole_numbers="1,2,18"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "method_name", "arguments", "expected_args", "expected_kwargs"),
    [
        ("get_available_badges", "get_available_badges", {}, (), {}),
        ("get_in_progress_badges", "get_in_progress_badges", {}, (), {}),
        ("get_gear_defaults", "get_gear_defaults", {"user_profile_number": "42"}, ("42",), {}),
        ("get_gear_stats", "get_gear_stats", {"gear_uuid": "gear-1"}, ("gear-1",), {}),
        ("get_golf_summary", "get_golf_summary", {"start": 2, "limit": 3}, (), {"start": 2, "limit": 3}),
        ("get_golf_scorecard", "get_golf_scorecard", {"scorecard_id": 99}, (99,), {}),
        ("get_intensity_minutes_data", "get_intensity_minutes_data", {"date": "2024-01-15"}, ("2024-01-15",), {}),
        ("get_last_activity", "get_last_activity", {}, (), {}),
        ("get_max_metrics", "get_max_metrics", {"date": "2024-01-15"}, ("2024-01-15",), {}),
        ("get_scheduled_workout_by_id", "get_scheduled_workout_by_id", {"scheduled_workout_id": 7}, (7,), {}),
        ("get_training_plans", "get_training_plans", {}, (), {}),
        ("get_training_plan_by_id", "get_training_plan_by_id", {"plan_id": 11}, (11,), {}),
        ("get_adaptive_training_plan_by_id", "get_adaptive_training_plan_by_id", {"plan_id": 12}, (12,), {}),
    ],
)
async def test_read_parity_tools_forward_upstream_calls(
    app_with_consumer_parity,
    mock_garmin_client,
    tool_name,
    method_name,
    arguments,
    expected_args,
    expected_kwargs,
):
    getattr(mock_garmin_client, method_name).return_value = {"method": method_name}

    result = await app_with_consumer_parity.call_tool(tool_name, arguments)

    data = tool_data(result)
    assert data["status"] == "ok"
    assert data["data"] == {"method": method_name}
    getattr(mock_garmin_client, method_name).assert_called_once_with(
        *expected_args, **expected_kwargs
    )


@pytest.mark.asyncio
async def test_running_tolerance_validates_date_and_aggregation(
    app_with_consumer_parity, mock_garmin_client
):
    mock_garmin_client.get_running_tolerance.return_value = [{"date": "2024-01-15"}]

    result = await app_with_consumer_parity.call_tool(
        "get_running_tolerance",
        {
            "start_date": "2024-01-01",
            "end_date": "2024-01-15",
            "aggregation": "daily",
        },
    )

    assert tool_data(result)["status"] == "ok"
    mock_garmin_client.get_running_tolerance.assert_called_once_with(
        "2024-01-01", "2024-01-15", aggregation="daily"
    )

    invalid = await app_with_consumer_parity.call_tool(
        "get_running_tolerance",
        {
            "start_date": "2024-01-01",
            "end_date": "2024-01-15",
            "aggregation": "monthly",
        },
    )
    assert tool_data(invalid)["status"] == "invalid_argument"


@pytest.mark.asyncio
async def test_not_available_client_method_is_explicit():
    client = Mock(spec=[])
    consumer_parity.configure(client)
    app = consumer_parity.register_tools(FastMCP("Unavailable Consumer Parity"))

    result = await app.call_tool("get_golf_summary", {})

    data = tool_data(result)
    assert data["status"] == "unavailable"
    assert "get_golf_summary" in data["error"]
