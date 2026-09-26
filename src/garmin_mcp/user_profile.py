"""
User Profile functions for Garmin Connect MCP Server
"""
import json
import datetime
import re
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None

_HEART_RATE_ZONES_URL = "/biometric-service/heartRateZones"


def _normalize_hr_zone_sport(sport: str) -> str:
    """Convert a caller-friendly sport name to Garmin's uppercase sport key."""
    normalized = sport.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized == "GENERIC":
        normalized = "DEFAULT"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized):
        raise ValueError(
            "sport must be a non-empty Garmin sport key such as DEFAULT, RUNNING, or CYCLING"
        )
    return normalized


def _get_heart_rate_zone_configs() -> List[Dict[str, Any]]:
    """Read the saved per-sport zone configuration from Garmin Connect."""
    zones = garmin_client.connectapi(_HEART_RATE_ZONES_URL)
    if not isinstance(zones, list):
        raise ValueError("Garmin returned an unexpected heart-rate-zone response")
    return zones


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all user profile tools with the MCP server app"""
    
    @app.tool()
    async def get_full_name() -> str:
        """Get user's full name from profile"""
        try:
            full_name = garmin_client.get_full_name()
            return json.dumps({"full_name": full_name}, indent=2)
        except Exception as e:
            return f"Error retrieving user's full name: {str(e)}"

    @app.tool()
    async def get_unit_system() -> str:
        """Get user's preferred unit system from profile"""
        try:
            unit_system = garmin_client.get_unit_system()
            return json.dumps({"unit_system": unit_system}, indent=2)
        except Exception as e:
            return f"Error retrieving unit system: {str(e)}"
    
    @app.tool()
    async def get_user_profile() -> str:
        """Get user profile information"""
        try:
            profile = garmin_client.get_user_profile()
            if not profile:
                return "No user profile information found."
            return json.dumps(profile, indent=2)
        except Exception as e:
            return f"Error retrieving user profile: {str(e)}"

    @app.tool()
    async def get_userprofile_settings() -> str:
        """Get user profile settings"""
        try:
            settings = garmin_client.get_userprofile_settings()
            if not settings:
                return "No user profile settings found."
            return json.dumps(settings, indent=2)
        except Exception as e:
            return f"Error retrieving user profile settings: {str(e)}"

    @app.tool()
    async def get_heart_rate_zones(sport: Optional[str] = None) -> str:
        """Get the user's saved heart-rate training-zone configuration.

        Garmin stores a generic DEFAULT profile plus optional sport-specific
        overrides such as RUNNING and CYCLING. With no sport, this returns every
        saved profile; pass a sport key to return just that profile.

        Args:
            sport: Optional Garmin sport key (for example default, running, or
                   cycling). "generic" is accepted as an alias for DEFAULT.
        """
        try:
            zones = _get_heart_rate_zone_configs()
            if sport is None:
                if not zones:
                    return "No configured heart-rate zones found."
                return json.dumps(zones, indent=2)

            sport_key = _normalize_hr_zone_sport(sport)
            configured = next(
                (zone for zone in zones if zone.get("sport") == sport_key), None
            )
            if configured is None:
                return f"No configured heart-rate zones found for sport {sport_key}."
            return json.dumps(configured, indent=2)
        except Exception as e:
            return f"Error retrieving heart-rate zones: {str(e)}"

    return app
