"""Guard the registered MCP surface against upstream method drift."""

import asyncio
import inspect
from unittest.mock import Mock

from garminconnect import Garmin
from mcp.server.fastmcp import FastMCP

from garmin_mcp import (
    activity_analysis,
    activity_management,
    challenges,
    consumer_parity,
    consumer_writes,
    courses,
    data_management,
    devices,
    gear_management,
    health_wellness,
    nutrition,
    training,
    user_profile,
    weight_management,
    womens_health,
    workout_builders,
    workout_templates,
    workouts,
)


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
]

INTERNAL_UPSTREAM_METHODS = {
    "connectapi",
    "connectwebproxy",
    "download",
    "login",
    "logout",
    "query_garmin_graphql",
    "resume_login",
}

CUSTOM_EQUIVALENTS = {
    "download_activity": "download_activity_file",
    "upload_cycling_workout": "upload_workout",
    "upload_hiking_workout": "upload_workout",
    "upload_running_workout": "upload_workout",
    "upload_swimming_workout": "upload_workout",
    "upload_walking_workout": "upload_workout",
}


def _registered_tool_names() -> set[str]:
    app = FastMCP("upstream parity")
    client = Mock()
    for module in MODULES:
        module.configure(client)
        app = module.register_tools(app)
    app = workout_templates.register_resources(app)
    return asyncio.run(_list_names(app))


async def _list_names(app) -> set[str]:
    return {tool.name for tool in await app.list_tools()}


def test_every_upstream_consumer_method_has_a_parity_disposition():
    upstream_methods = {
        name
        for name, value in inspect.getmembers(Garmin)
        if not name.startswith("_")
        and name != "typed"
        and (inspect.isfunction(value) or inspect.ismethoddescriptor(value))
    }
    registered = _registered_tool_names()
    direct_exposure_gaps = (
        upstream_methods
        - INTERNAL_UPSTREAM_METHODS
        - set(CUSTOM_EQUIVALENTS)
        - registered
    )

    assert not direct_exposure_gaps
    assert all(tool_name in registered for tool_name in CUSTOM_EQUIVALENTS.values())
    assert len(registered) == 160
