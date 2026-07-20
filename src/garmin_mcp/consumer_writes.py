"""Confirmation-gated write wrappers for upstream Garmin consumer methods."""

import json
from pathlib import Path
from typing import Any


garmin_client = None
ACTIVITY_FILE_EXTENSIONS = {"fit", "gpx", "tcx"}


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


def _compact_response(value: Any) -> Any:
    """Return a bounded summary of a write response."""
    if value is None:
        return None
    if isinstance(value, dict):
        selected = {
            key: value[key]
            for key in (
                "activityId",
                "activity_id",
                "workoutId",
                "fileName",
                "status",
                "success",
                "message",
            )
            if key in value
        }
        selected["response_keys"] = sorted(str(key) for key in value.keys())[:30]
        return selected
    if isinstance(value, list):
        return {"response_type": "list", "count": len(value)}
    status_code = getattr(value, "status_code", None)
    if status_code is not None:
        return {"response_type": type(value).__name__, "status_code": status_code}
    return {"response_type": type(value).__name__}


def _result(method: str, status: str, **fields: Any) -> str:
    return json.dumps({"status": status, "method": method, **fields}, indent=2, default=str)


def _error(method: str, error: Exception, status: str = "error") -> str:
    return _result(method, status, error=str(error))


def _exception_status_code(error: Exception) -> int | None:
    """Extract an HTTP status from common garminconnect error shapes."""
    candidates = [
        error,
        getattr(error, "response", None),
        getattr(getattr(error, "error", None), "response", None),
    ]
    for candidate in candidates:
        value = getattr(candidate, "status_code", None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _is_not_found(error: Exception) -> bool:
    return _exception_status_code(error) == 404 or "not found" in str(error).lower()


_RECORD_IDENTIFIER_KEYS = {
    "id",
    "version",
    "measurementid",
    "measurementversion",
    "weightpk",
    "weight_pk",
}


def _contains_record_identifier(value: Any, identifier: str) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _RECORD_IDENTIFIER_KEYS and str(child) == identifier:
                return True
            if _contains_record_identifier(child, identifier):
                return True
    elif isinstance(value, list):
        return any(_contains_record_identifier(child, identifier) for child in value)
    return False


def _confirmation_required(method: str, target: dict[str, Any], warning: str) -> str:
    return _result(
        method,
        "confirmation_required",
        target=target,
        warning=warning,
        next_step="Repeat the call with confirm=true to perform this mutation.",
    )


def _file_target(activity_path: str) -> tuple[Path, dict[str, Any]]:
    path_value = _required_text(activity_path, "activity_path")
    path = Path(path_value).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Activity file does not exist: {path.name}")
    if not path.is_file():
        raise ValueError("activity_path must identify a file")
    extension = path.suffix.lower().lstrip(".")
    if extension not in ACTIVITY_FILE_EXTENSIONS:
        raise ValueError("activity_path must be a FIT, GPX, or TCX file")
    return path, {
        "file_name": path.name,
        "extension": extension,
        "size_bytes": path.stat().st_size,
    }


def _verify_exercise_sets(activity_id: int) -> dict[str, Any]:
    try:
        value = _invoke("get_activity_exercise_sets", activity_id)
        if isinstance(value, dict):
            exercise_sets = value.get("exerciseSets")
            return {
                "status": "verified",
                "exercise_set_count": len(exercise_sets)
                if isinstance(exercise_sets, list)
                else None,
            }
        return {"status": "verified", "response_type": type(value).__name__}
    except _CapabilityUnavailable as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _verify_gear_default(activity_type: str, gear_uuid: str, default_gear: bool) -> dict[str, Any]:
    try:
        device = _invoke("get_device_last_used")
        profile_number = device.get("userProfileNumber") if isinstance(device, dict) else None
        if profile_number is None:
            return {"status": "unverified", "reason": "user_profile_number_unavailable"}
        defaults = _invoke("get_gear_defaults", str(profile_number))
    except _CapabilityUnavailable as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    if not isinstance(defaults, list):
        return {"status": "unverified", "reason": "unexpected_defaults_shape"}
    matching = [
        item
        for item in defaults
        if isinstance(item, dict)
        and item.get("uuid") == gear_uuid
        and str(item.get("activityTypePk")) == str(activity_type)
    ]
    if default_gear:
        return {
            "status": "verified" if matching else "unverified",
            "matching_default_count": len(matching),
        }
    return {
        "status": "verified" if not matching else "unverified",
        "matching_default_count": len(matching),
    }


def _verify_activity_deleted(activity_id: int) -> dict[str, Any]:
    try:
        value = _invoke("get_activity", activity_id)
        return {"status": "verified" if not value else "unverified"}
    except Exception as exc:
        if _is_not_found(exc):
            return {"status": "verified", "verification_error": type(exc).__name__}
        return {"status": "unverified", "verification_error": type(exc).__name__}


def _verify_date_record_deleted(
    method: str, date: str, identifier: str
) -> dict[str, Any]:
    try:
        value = _invoke(method, date, date)
        return {
            "status": "verified"
            if not _contains_record_identifier(value, identifier)
            else "unverified"
        }
    except _CapabilityUnavailable as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unverified", "verification_error": type(exc).__name__}


def register_tools(app):
    """Register confirmation-gated consumer-client write tools."""

    @app.tool()
    async def create_manual_activity_from_json(
        payload: dict[str, Any],
        confirm: bool = False,
    ) -> str:
        """Create a manual activity from a Garmin-compatible JSON payload.

        The operation is preview-only until ``confirm=true`` is supplied.
        """
        method = "create_manual_activity_from_json"
        try:
            if not isinstance(payload, dict) or not payload:
                raise ValueError("payload must be a non-empty object")
            target = {
                "activity_name": payload.get("activityName"),
                "activity_type": (payload.get("activityTypeDTO") or {}).get("typeKey")
                if isinstance(payload.get("activityTypeDTO"), dict)
                else None,
                "payload_keys": sorted(str(key) for key in payload.keys()),
            }
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This creates a new Garmin activity and cannot be silently undone.",
                )
            response = _invoke(method, payload)
            return _result(method, "ok", target=target, response=_compact_response(response))
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    async def _upload_activity(activity_path: str, confirm: bool, method: str) -> str:
        try:
            path, target = _file_target(activity_path)
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This uploads a new activity to Garmin Connect and may create a duplicate.",
                )
            response = _invoke(method, str(path))
            return _result(method, "ok", target=target, response=_compact_response(response))
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except (FileNotFoundError, ValueError) as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    @app.tool()
    async def upload_activity(activity_path: str, confirm: bool = False) -> str:
        """Upload a FIT, GPX, or TCX activity file after explicit confirmation."""
        return await _upload_activity(activity_path, confirm, "upload_activity")

    @app.tool()
    async def import_activity(activity_path: str, confirm: bool = False) -> str:
        """Import a FIT, GPX, or TCX activity file after explicit confirmation."""
        return await _upload_activity(activity_path, confirm, "import_activity")

    @app.tool()
    async def set_activity_exercise_sets(
        activity_id: int,
        payload: dict[str, Any],
        confirm: bool = False,
    ) -> str:
        """Replace all exercise sets for a strength activity after confirmation."""
        method = "set_activity_exercise_sets"
        try:
            activity_id = _positive_int(activity_id, "activity_id")
            if not isinstance(payload, dict) or not isinstance(payload.get("exerciseSets"), list):
                raise ValueError("payload.exerciseSets must be a list")
            target = {
                "activity_id": activity_id,
                "exercise_set_count": len(payload["exerciseSets"]),
            }
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This replaces all existing exercise sets for the selected activity.",
                )
            response = _invoke(method, activity_id, payload)
            return _result(
                method,
                "ok",
                target=target,
                response=_compact_response(response),
                postcondition=_verify_exercise_sets(activity_id),
            )
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    @app.tool()
    async def set_gear_default(
        activity_type: str,
        gear_uuid: str,
        default_gear: bool = True,
        confirm: bool = False,
    ) -> str:
        """Set or remove a gear default after explicit confirmation."""
        method = "set_gear_default"
        try:
            activity_type = _required_text(activity_type, "activity_type")
            gear_uuid = _required_text(gear_uuid, "gear_uuid")
            target = {
                "activity_type": activity_type,
                "gear_uuid": gear_uuid,
                "default_gear": default_gear,
            }
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This changes the default gear assignment for an activity type.",
                )
            response = _invoke(method, activity_type, gear_uuid, default_gear=default_gear)
            return _result(
                method,
                "ok",
                target=target,
                response=_compact_response(response),
                postcondition=_verify_gear_default(activity_type, gear_uuid, default_gear),
            )
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    @app.tool()
    async def delete_activity(activity_id: int, confirm: bool = False) -> str:
        """Permanently delete one activity after exact-target confirmation."""
        method = "delete_activity"
        try:
            activity_id = _positive_int(activity_id, "activity_id")
            activity = _invoke("get_activity", activity_id)
            target = {
                "activity_id": activity_id,
                "name": activity.get("activityName") if isinstance(activity, dict) else None,
                "start_time": (
                    (activity.get("summaryDTO") or {}).get("startTimeLocal")
                    if isinstance(activity, dict)
                    else None
                ),
            }
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This permanently deletes the selected Garmin activity and cannot be undone.",
                )
            response = _invoke(method, str(activity_id))
            return _result(
                method,
                "ok",
                target=target,
                response=_compact_response(response),
                postcondition=_verify_activity_deleted(activity_id),
            )
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    @app.tool()
    async def delete_blood_pressure(
        version: str,
        date: str,
        confirm: bool = False,
    ) -> str:
        """Delete one exact blood-pressure record after confirmation."""
        method = "delete_blood_pressure"
        try:
            version = _required_text(version, "version")
            date = _required_text(date, "date")
            preflight = _invoke("get_blood_pressure", date, date)
            target = {"version": version, "date": date}
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This permanently deletes one exact blood-pressure record; verify version and date before confirming.",
                )
            response = _invoke(method, version, date)
            return _result(
                method,
                "ok",
                target=target,
                preflight_available=preflight is not None,
                response=_compact_response(response),
                postcondition=_verify_date_record_deleted(
                    "get_blood_pressure", date, version
                ),
            )
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    @app.tool()
    async def delete_weigh_in(
        weight_pk: str,
        date: str,
        confirm: bool = False,
    ) -> str:
        """Delete one exact weigh-in after confirmation."""
        method = "delete_weigh_in"
        try:
            weight_pk = _required_text(weight_pk, "weight_pk")
            date = _required_text(date, "date")
            preflight = _invoke("get_daily_weigh_ins", date)
            target = {"weight_pk": weight_pk, "date": date}
            if not confirm:
                return _confirmation_required(
                    method,
                    target,
                    "This permanently deletes one exact weigh-in; verify the primary key and date before confirming.",
                )
            response = _invoke(method, weight_pk, date)
            return _result(
                method,
                "ok",
                target=target,
                preflight_available=preflight is not None,
                response=_compact_response(response),
                postcondition=_verify_date_record_deleted(
                    "get_weigh_ins", date, weight_pk
                ),
            )
        except _CapabilityUnavailable as exc:
            return _error(method, exc, status="unavailable")
        except ValueError as exc:
            return _error(method, exc, status="invalid_argument")
        except Exception as exc:
            return _error(method, exc)

    return app
