"""Confirmation coverage for course mutations."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import courses


@pytest.fixture
def app_with_courses(mock_garmin_client):
    courses.configure(mock_garmin_client)
    app = FastMCP("Test Courses")
    return courses.register_tools(app)


@pytest.mark.asyncio
async def test_upload_course_requires_confirmation(app_with_courses, mock_garmin_client, tmp_path):
    gpx_path = tmp_path / "route.gpx"
    gpx_path.write_text("<gpx />", encoding="utf-8")

    result = await app_with_courses.call_tool(
        "upload_course",
        {"gpx_path": str(gpx_path), "confirm": False},
    )

    payload = json.loads(result[0][0].text)
    assert payload["status"] == "confirmation_required"
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_delete_course_requires_confirmation(app_with_courses, mock_garmin_client):
    result = await app_with_courses.call_tool(
        "delete_course", {"course_id": 123, "confirm": False}
    )

    payload = json.loads(result[0][0].text)
    assert payload["status"] == "confirmation_required"
    mock_garmin_client.client.delete.assert_not_called()
