"""
Course management functions for Garmin Connect MCP Server.

Adds support for uploading GPX files as Garmin Connect Courses. The underlying
Garmin Connect endpoint is undocumented; this module reverse-engineers the
two-step flow used by the web UI:

    1) POST /course-service/course/import   (multipart upload of the GPX)
       -> returns a parsed course skeleton with geoPoints but no distance
          / bounding box / start point

    2) POST /course-service/course          (JSON, the actual save)
       -> server enriches with elevation gain/loss from terrain DB and
          returns the saved course with a courseId.

Both calls require the same OAuth2 bearer the rest of the MCP already uses.
"""

import io
import json
import math
import os
import pathlib
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape, quoteattr

from garmin_mcp.mutation_safety import confirmation_required

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


_EARTH_RADIUS_M = 6371000.0


def _haversine(p1: Dict[str, float], p2: Dict[str, float]) -> float:
    lat1, lon1 = math.radians(p1["latitude"]), math.radians(p1["longitude"])
    lat2, lon2 = math.radians(p2["latitude"]), math.radians(p2["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _initial_bearing(p1: Dict[str, float], p2: Dict[str, float]) -> float:
    lat1, lat2 = math.radians(p1["latitude"]), math.radians(p2["latitude"])
    dlon = math.radians(p2["longitude"] - p1["longitude"])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# Map common activity keys to the Garmin activity type id Garmin's course
# service understands. The id list is small and stable.
_ACTIVITY_TYPE_IDS = {
    "running": 1,
    "cycling": 2,
    "hiking": 3,
    "walking": 9,
    "trail_running": 6,
    "mountain_biking": 5,
    "road_biking": 10,
    "gravel_cycling": 4,
}


def upload_course_file(
    gpx_path: str,
    *,
    course_name: Optional[str] = None,
    activity_type: str = "running",
    description: Optional[str] = None,
) -> dict[str, Any]:
    """Upload a GPX file as a Garmin course and return structured metadata."""
    if garmin_client is None:
        raise RuntimeError("Course module is not configured with a Garmin client")
    path = pathlib.Path(gpx_path)
    if path.suffix.lower() != ".gpx":
        raise ValueError(
            f"only .gpx files are allowed, got: {path.suffix or '(no extension)'}"
        )
    gpx_path = str(path.resolve())
    if not os.path.isfile(gpx_path):
        raise FileNotFoundError(f"GPX file not found: {gpx_path}")

    activity_type_id = _ACTIVITY_TYPE_IDS.get(activity_type.lower())
    if activity_type_id is None:
        raise ValueError(
            f"unknown activity_type '{activity_type}'. "
            f"Supported: {', '.join(sorted(_ACTIVITY_TYPE_IDS))}."
        )

    with open(gpx_path, "rb") as f:
        gpx_bytes = f.read()

    parsed = garmin_client.client.post(
        "connectapi",
        "/course-service/course/import",
        files={
            "file": (
                os.path.basename(gpx_path),
                gpx_bytes,
                "application/gpx+xml",
            )
        },
        api=True,
    )

    effective_name = (
        course_name
        or parsed.get("courseName")
        or os.path.splitext(os.path.basename(gpx_path))[0]
    )
    payload = _build_course_payload(
        parsed,
        course_name=effective_name,
        activity_type_id=activity_type_id,
        description=description,
    )
    saved = garmin_client.client.post(
        "connectapi", "/course-service/course", json=payload, api=True,
    )
    return {
        "status": "success",
        "course_id": saved.get("courseId"),
        "name": saved.get("courseName"),
        "distance_m": saved.get("distanceMeter"),
        "elevation_gain_m": saved.get("elevationGainMeter"),
        "elevation_loss_m": saved.get("elevationLossMeter"),
        "activity_type_id": saved.get("activityTypePk"),
        "url": f"https://connect.{garmin_client.client.domain}/modern/course/{saved.get('courseId')}",
    }


def _build_course_payload(
    parsed: Dict[str, Any],
    course_name: str,
    activity_type_id: int,
    description: Optional[str],
) -> Dict[str, Any]:
    """Construct the create-course JSON body from the /import response."""

    geo_points = list(parsed.get("geoPoints") or [])
    if len(geo_points) < 2:
        raise ValueError("Parsed course has fewer than 2 geo points; GPX is empty or invalid")

    # Compute cumulative distance per point + total
    total_distance = 0.0
    for i, p in enumerate(geo_points):
        if i == 0:
            p["distance"] = 0.0
        else:
            total_distance += _haversine(geo_points[i - 1], p)
            p["distance"] = total_distance
        if p.get("elevation") is None:
            p["elevation"] = 0.0

    lats = [p["latitude"] for p in geo_points]
    lons = [p["longitude"] for p in geo_points]

    bbox = {
        "center": {
            "latitude": (min(lats) + max(lats)) / 2,
            "longitude": (min(lons) + max(lons)) / 2,
        },
        "lowerLeft": {"latitude": min(lats), "longitude": min(lons)},
        "upperRight": {"latitude": max(lats), "longitude": max(lons)},
        "lowerLeftLatIsSet": True,
        "lowerLeftLongIsSet": True,
        "upperRightLatIsSet": True,
        "upperRightLongIsSet": True,
    }

    start_point = {
        "latitude": geo_points[0]["latitude"],
        "longitude": geo_points[0]["longitude"],
        "elevation": geo_points[0].get("elevation") or 0.0,
        "distance": None,
        "timestamp": None,
    }

    bearing = _initial_bearing(geo_points[0], geo_points[-1])

    return {
        "courseName": course_name,
        "description": description,
        "openStreetMap": False,
        "matchedToSegments": False,
        "userProfilePk": None,
        "userGroupPk": None,
        "rulePK": 2,  # private
        "geoRoutePk": None,
        "sourceTypeId": 3,  # GPX
        "sourcePk": None,
        "distanceMeter": total_distance,
        "elevationGainMeter": 0.0,
        "elevationLossMeter": 0.0,
        "startPoint": start_point,
        "coursePoints": [],
        "boundingBox": bbox,
        "hasShareableEvent": False,
        "hasTurnDetectionDisabled": False,
        "activityTypePk": activity_type_id,
        "virtualPartnerId": None,
        "includeLaps": False,
        "elapsedSeconds": None,
        "speedMeterPerSecond": None,
        "courseLines": [
            {
                "courseId": None,
                "sortOrder": 1,
                "numberOfPoints": len(geo_points),
                "distanceInMeters": total_distance,
                "bearing": bearing,
                "points": geo_points,
                "coordinateSystem": "WGS84",
                "originalCoordinateSystem": "WGS84",
            }
        ],
        "coordinateSystem": "WGS84",
        "targetCoordinateSystem": "WGS84",
        "originalCoordinateSystem": "WGS84",
        "consumer": None,
        "elevationSource": 3,
        "hasPaceBand": False,
        "hasPowerGuide": False,
        "favorite": False,
        "startNote": None,
        "finishNote": None,
        "cutoffDuration": None,
        "geoPoints": geo_points,
    }


def _resolve_gpx_output_path(course_id: int, output_path: Optional[str] = None) -> str:
    """Resolve the destination file path for downloading a course GPX."""
    if output_path:
        path = os.path.abspath(os.path.expanduser(output_path))
        if os.path.isdir(path) or output_path.endswith(("/", "\\")):
            return os.path.join(path, f"{course_id}.gpx")
        return path

    download_dir = os.getenv("GARMIN_FIT_DOWNLOAD_DIR")
    base_dir = os.path.abspath(os.path.expanduser(download_dir or "./courses"))
    return os.path.join(base_dir, f"{course_id}.gpx")


def register_tools(app):
    """Register course management tools"""

    @app.tool()
    async def get_courses() -> str:
        """List all courses saved on Garmin Connect.

        Returns a curated list of courses with id, name, distance, activity type
        and creation date.
        """
        try:
            data = garmin_client.client.connectapi("/course-service/course")

            if not isinstance(data, list):
                return json.dumps(data, indent=2)

            curated = [
                {
                    "course_id": c.get("courseId"),
                    "name": c.get("courseName"),
                    "distance_m": c.get("distanceInMeters"),
                    "elevation_gain_m": c.get("elevationGainInMeters"),
                    "elevation_loss_m": c.get("elevationLossInMeters"),
                    "activity": (c.get("activityType") or {}).get("typeKey"),
                    "has_pace_band": c.get("hasPaceBand"),
                    "created": c.get("createdDateFormatted"),
                }
                for c in data
            ]
            return json.dumps({"count": len(curated), "courses": curated}, indent=2)
        except Exception as e:
            return f"Error listing courses: {str(e)}"

    @app.tool()
    async def get_course_details(course_id: int) -> str:
        """Get full details of a Garmin Connect course by ID.

        Returns course metadata and custom course waypoints.

        Args:
            course_id: ID of the course (from get_courses).
        """
        try:
            data = garmin_client.client.connectapi(
                f"/course-service/course/{course_id}"
            )
            if not isinstance(data, dict):
                return json.dumps(data, indent=2)

            raw_course_points = data.get("coursePoints")
            course_points = raw_course_points if isinstance(raw_course_points, list) else []
            raw_geo_points = data.get("geoPoints")
            geo_points = raw_geo_points if isinstance(raw_geo_points, list) else []
            activity = data.get("activityType") or {}
            domain = getattr(garmin_client.client, "domain", None)
            result = {
                "course_id": data.get("courseId"),
                "name": data.get("courseName"),
                "distance_m": (
                    data.get("distanceInMeters")
                    if data.get("distanceInMeters") is not None
                    else data.get("distanceMeter")
                ),
                "elevation_gain_m": (
                    data.get("elevationGainInMeters")
                    if data.get("elevationGainInMeters") is not None
                    else data.get("elevationGainMeter")
                ),
                "elevation_loss_m": (
                    data.get("elevationLossInMeters")
                    if data.get("elevationLossInMeters") is not None
                    else data.get("elevationLossMeter")
                ),
                "activity": activity.get("typeKey"),
                "waypoints_count": len(course_points),
                "waypoints": [
                    {
                        "name": point.get("name"),
                        "type": point.get("pointType"),
                        "lat": point.get("lat"),
                        "lon": point.get("lon"),
                        "distance_m": point.get("distance"),
                    }
                    for point in course_points
                    if isinstance(point, dict)
                ],
                "geo_points_count": len(geo_points),
            }
            if domain:
                result["url"] = (
                    f"https://connect.{domain}/modern/course/{course_id}"
                )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error fetching course details: {str(e)}"

    @app.tool()
    async def download_course_gpx(
        course_id: int,
        output_path: Optional[str] = None,
    ) -> str:
        """Download a Garmin course as a GPX 1.1 file on the local machine.

        Args:
            course_id: ID of the course to download.
            output_path: Optional destination file or directory. Defaults to
                GARMIN_FIT_DOWNLOAD_DIR or ./courses/{course_id}.gpx.
        """
        try:
            data = garmin_client.client.connectapi(
                f"/course-service/course/{course_id}"
            )
            if not isinstance(data, dict) or not isinstance(data.get("geoPoints"), list):
                return f"Error: course {course_id} not found or missing geoPoints."

            target_path = _resolve_gpx_output_path(course_id, output_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            name = data.get("courseName") or f"Garmin Course {course_id}"
            geo_points = data["geoPoints"]
            raw_course_points = data.get("coursePoints")
            course_points = raw_course_points if isinstance(raw_course_points, list) else []
            lines = [
                "<?xml version='1.0' encoding='UTF-8'?>",
                "<gpx version='1.1' creator='GarminConnectMCP' xmlns='http://www.topografix.com/GPX/1/1'>",
                "  <metadata>",
                f"    <name>{escape(str(name))}</name>",
                "  </metadata>",
            ]

            written_waypoints = 0
            for point in course_points:
                if not isinstance(point, dict):
                    continue
                lat, lon = point.get("lat"), point.get("lon")
                if lat is None or lon is None:
                    continue
                point_name = point.get("name") or point.get("pointType") or "Waypoint"
                point_type = point.get("pointType") or "GENERIC"
                lines.append(
                    f"  <wpt lat={quoteattr(str(lat))} lon={quoteattr(str(lon))}>"
                )
                lines.append(f"    <name>{escape(str(point_name))}</name>")
                lines.append(f"    <type>{escape(str(point_type))}</type>")
                lines.append("  </wpt>")
                written_waypoints += 1

            lines.extend(["  <trk>", f"    <name>{escape(str(name))}</name>", "    <trkseg>"])
            written_track_points = 0
            for point in geo_points:
                if not isinstance(point, dict):
                    continue
                lat, lon = point.get("latitude"), point.get("longitude")
                if lat is None or lon is None:
                    continue
                elevation = point.get("elevation")
                elevation_tag = (
                    f"<ele>{escape(str(elevation))}</ele>"
                    if elevation is not None
                    else ""
                )
                lines.append(
                    f"      <trkpt lat={quoteattr(str(lat))} lon={quoteattr(str(lon))}>"
                    f"{elevation_tag}</trkpt>"
                )
                written_track_points += 1
            lines.extend(["    </trkseg>", "  </trk>", "</gpx>"])

            with open(target_path, "w", encoding="utf-8") as output_file:
                output_file.write("\n".join(lines))

            return json.dumps(
                {
                    "status": "success",
                    "course_id": course_id,
                    "name": name,
                    "gpx_path": target_path,
                    "waypoints_count": written_waypoints,
                    "track_points_count": written_track_points,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error downloading course GPX: {str(e)}"

    @app.tool()
    async def upload_course(
        gpx_path: str,
        course_name: Optional[str] = None,
        activity_type: str = "running",
        description: Optional[str] = None,
        confirm: bool = False,
    ) -> str:
        """Upload a GPX file as a Garmin Connect Course.

        The course can then be loaded onto the watch (sync or "Send to Device")
        and used as a navigation course or to build a PacePro strategy.

        Args:
            gpx_path: Absolute path to the .gpx file on disk.
            course_name: Override the course name. Defaults to the name parsed
                from the GPX file.
            activity_type: One of running, cycling, hiking, walking, trail_running,
                mountain_biking, road_biking, gravel_cycling. Defaults to running.
            description: Optional description shown on the course detail page.
            confirm: Must be true to upload and save the course.
        """
        try:
            path = pathlib.Path(gpx_path)
            if path.suffix.lower() != ".gpx":
                raise ValueError(
                    f"only .gpx files are allowed, got: {path.suffix or '(no extension)'}"
                )
            if not path.is_file():
                raise FileNotFoundError(f"GPX file not found: {path.resolve()}")
            if not confirm:
                return confirmation_required(
                    "upload_course",
                    {
                        "gpx_path": str(path.resolve()),
                        "course_name": course_name,
                        "activity_type": activity_type,
                    },
                    "This uploads and saves a new Garmin Connect course.",
                )
            saved = upload_course_file(
                gpx_path,
                course_name=course_name,
                activity_type=activity_type,
                description=description,
            )
            return json.dumps(saved, indent=2)

        except Exception as e:
            return f"Error uploading course: {str(e)}"

    @app.tool()
    async def delete_course(course_id: int, confirm: bool = False) -> str:
        """Delete a course from Garmin Connect.

        Args:
            course_id: ID of the course to delete (get IDs from get_courses).
            confirm: Must be true to permanently delete the course.
        """
        try:
            if not confirm:
                return confirmation_required(
                    "delete_course",
                    {"course_id": course_id},
                    "This permanently deletes the selected Garmin Connect course.",
                )
            garmin_client.client.delete(
                "connectapi", f"/course-service/course/{course_id}"
            )
            return json.dumps(
                {
                    "status": "success",
                    "course_id": course_id,
                    "message": f"Course {course_id} deleted",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error deleting course: {str(e)}"

    return app
