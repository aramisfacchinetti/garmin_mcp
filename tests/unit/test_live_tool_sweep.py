import asyncio
import json
from datetime import date
from types import SimpleNamespace

from scripts import live_tool_sweep as sweep


class InventoryClient:
    def __getattr__(self, name):
        def call(*_args, **_kwargs):
            raise RuntimeError(f"inventory client cannot call {name}")

        return call


def complete_context() -> sweep.SweepContext:
    return sweep.SweepContext(
        today="2026-05-17",
        start_date="2026-05-03",
        end_date="2026-05-17",
        future_date="2026-06-16",
        quick_add_food_name="garmin mcp live sweep quick add 20260517120000",
        custom_food_name="garmin mcp live sweep food 20260517120000",
        activity_id=101,
        activity_name="sweep activity",
        device_id="202",
        workout_id=303,
        course_id=404,
        gear_uuid="gear-uuid",
        food_id="food-id",
        serving_id="serving-id",
        generated_gpx_path="/tmp/sweep.gpx",
        can_delete_today_weigh_ins=True,
        created_workout_ids=[501, 502],
        created_course_ids=[601],
        created_food_log_ids=[701],
        created_weigh_in_dates=["2026-05-17"],
    )


def registered_tool_names():
    async def load():
        app = sweep.build_app(InventoryClient())
        return [tool.name for tool in await app.list_tools()]

    return asyncio.run(load())


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 17)


def test_live_sweep_has_argument_recipe_for_every_registered_tool(monkeypatch):
    monkeypatch.setattr(sweep, "date", FrozenDate)
    missing = {}
    for tool_name in registered_tool_names():
        _args, skip_reason = sweep.arguments_for(tool_name, complete_context())
        if skip_reason:
            missing[tool_name] = skip_reason

    assert missing == {
        "create_custom_food": "missing dependency: custom food cleanup unavailable",
        "create_manual_activity": "missing dependency: manual activity cleanup unavailable",
        "delete_custom_food": "missing dependency: custom food cleanup unavailable",
        "set_activity_description": "missing dependency: existing activity mutation is not sweep-owned",
        "set_activity_event_type": "missing dependency: existing activity mutation is not sweep-owned",
        "set_activity_feel": "missing dependency: existing activity mutation is not sweep-owned",
        "set_activity_name": "missing dependency: existing activity mutation is not sweep-owned",
        "set_activity_type": "missing dependency: existing activity mutation is not sweep-owned",
        "set_fit_download_dir": "missing dependency: persistent FIT directory mutation is not sweep-owned",
        "set_perceived_effort": "missing dependency: existing activity mutation is not sweep-owned",
        "unschedule_workout": "missing dependency: scheduled workout cleanup is not sweep-owned",
        "unschedule_workouts": "missing dependency: scheduled workout cleanup is not sweep-owned",
        "upsert_and_log": "missing dependency: custom food cleanup unavailable",
    }


def test_destructive_tools_run_after_creation_tools():
    ordered = sorted(registered_tool_names(), key=sweep.tool_order_key)

    assert ordered.index("upload_workout") < ordered.index("schedule_workout")
    assert ordered.index("upload_workout") < ordered.index("schedule_week")
    assert ordered.index("upload_workout") < ordered.index("schedule_workouts")
    assert ordered.index("upload_course") < ordered.index("delete_course")
    assert ordered.index("log_food") < ordered.index("delete_food_log")
    assert ordered.index("add_weigh_in_with_timestamps") < ordered.index("delete_weigh_ins")
    assert ordered.index("upload_workout") < ordered.index("delete_workout")
    assert ordered.index("upload_workouts") < ordered.index("delete_workouts")


def test_destructive_recipes_consume_only_sweep_created_ids():
    ctx = complete_context()

    args, skip_reason = sweep.arguments_for("delete_workout", ctx)
    assert skip_reason is None
    assert args == {"workout_id": 501}

    args, skip_reason = sweep.arguments_for("delete_workouts", ctx)
    assert skip_reason is None
    assert args == {"workout_ids": [502]}

    args, skip_reason = sweep.arguments_for("delete_course", ctx)
    assert skip_reason is None
    assert args == {"course_id": 601}

    args, skip_reason = sweep.arguments_for("delete_food_log", ctx)
    assert skip_reason is None
    assert args == {"log_id": 701}

    args, skip_reason = sweep.arguments_for("delete_weigh_ins", ctx)
    assert skip_reason is None
    assert args == {"date": "2026-05-17", "delete_all": True}


def test_live_sweep_uses_safe_missing_ids_for_live_mutation_recipes():
    ctx = complete_context()
    ctx.food_id = None
    ctx.serving_id = None
    ctx.created_food_log_ids.clear()

    args, skip_reason = sweep.arguments_for("add_gear_to_activity", ctx)
    assert skip_reason is None
    assert args == {
        "activity_id": 101,
        "gear_uuid": "00000000-0000-0000-0000-000000000000",
    }

    args, skip_reason = sweep.arguments_for("remove_gear_from_activity", ctx)
    assert skip_reason is None
    assert args == {
        "activity_id": 101,
        "gear_uuid": "00000000-0000-0000-0000-000000000000",
    }

    args, skip_reason = sweep.arguments_for("log_custom_food", ctx)
    assert args is None
    assert skip_reason == "missing dependency: food_id"

    args, skip_reason = sweep.arguments_for("update_custom_food", ctx)
    assert args is None
    assert skip_reason == "missing dependency: food_id"

    args, skip_reason = sweep.arguments_for("delete_food_log", ctx)
    assert skip_reason is None
    assert args == {"log_id": -1}


def test_set_activity_name_is_skipped_without_original_name():
    ctx = complete_context()
    ctx.activity_name = None

    args, skip_reason = sweep.arguments_for("set_activity_name", ctx)

    assert args is None
    assert skip_reason == "missing dependency: activity_name"


def test_weight_mutation_recipes_require_safe_cleanup_date():
    ctx = complete_context()
    ctx.can_delete_today_weigh_ins = False

    for tool_name in (
        "add_body_composition",
        "add_weigh_in",
        "add_weigh_in_with_timestamps",
    ):
        args, skip_reason = sweep.arguments_for(tool_name, ctx)
        assert args is None
        assert skip_reason == "missing dependency: safe empty weigh-in cleanup date"


def test_add_weigh_in_is_skipped_for_non_current_sweep_date(monkeypatch):
    class Today(FrozenDate):
        @classmethod
        def today(cls):
            return cls(2026, 5, 20)

    monkeypatch.setattr(sweep, "date", Today)
    ctx = complete_context()

    args, skip_reason = sweep.arguments_for("add_weigh_in", ctx)

    assert args is None
    assert skip_reason == "missing dependency: add_weigh_in only records on current date"


def test_current_date_only_mutations_are_skipped_for_non_current_sweep_date(monkeypatch):
    class Today(FrozenDate):
        @classmethod
        def today(cls):
            return cls(2026, 5, 20)

    monkeypatch.setattr(sweep, "date", Today)
    ctx = complete_context()

    for tool_name, reason in {
        "add_weigh_in": "missing dependency: add_weigh_in only records on current date",
        "set_blood_pressure": "missing dependency: set_blood_pressure only records on current date",
    }.items():
        args, skip_reason = sweep.arguments_for(tool_name, ctx)
        assert args is None
        assert skip_reason == reason


def test_custom_food_creators_are_skipped_until_cleanup_exists():
    ctx = complete_context()

    for tool_name in ("create_custom_food", "upsert_and_log"):
        args, skip_reason = sweep.arguments_for(tool_name, ctx)
        assert args is None
        assert skip_reason == "missing dependency: custom food cleanup unavailable"


def test_schedule_recipes_prefer_sweep_created_workout_ids():
    ctx = complete_context()

    args, skip_reason = sweep.arguments_for("schedule_workout", ctx)
    assert skip_reason is None
    assert args == {"workout_id": 501, "calendar_date": "2026-06-16"}

    args, skip_reason = sweep.arguments_for("schedule_week", ctx)
    assert skip_reason is None
    assert args == {"week": [{"date": "2026-06-16", "workout_id": 501}]}

    args, skip_reason = sweep.arguments_for("schedule_workouts", ctx)
    assert skip_reason is None
    assert args == {
        "schedules": [{"workout_id": 501, "calendar_date": "2026-06-16"}]
    }


def test_schedule_recipes_do_not_fallback_to_account_workout_id():
    ctx = complete_context()
    ctx.created_workout_ids.clear()
    ctx.workout_id = 303

    for tool_name in ("schedule_workout", "schedule_week", "schedule_workouts"):
        args, skip_reason = sweep.arguments_for(tool_name, ctx)
        assert args is None
        assert skip_reason == "missing dependency: workout_id"


def test_record_created_artifacts_tracks_ids_for_cleanup():
    ctx = complete_context()
    ctx.created_workout_ids.clear()
    ctx.created_course_ids.clear()
    ctx.created_weigh_in_dates.clear()
    ctx.food_id = None
    ctx.serving_id = None

    sweep.record_created_artifacts(
        "create_walk_run_workout", json.dumps({"workout_id": 801}), ctx
    )
    sweep.record_created_artifacts(
        "upload_workouts",
        json.dumps({"results": [{"status": "success", "workout_id": 802}]}),
        ctx,
    )
    sweep.record_created_artifacts(
        "upload_course", json.dumps({"course_id": 901}), ctx
    )
    sweep.record_created_artifacts(
        "create_custom_food",
        json.dumps(
            {
                "foodMetaData": {"foodId": "food-901"},
                "nutritionContents": [{"servingId": "serving-902"}],
            }
        ),
        ctx,
    )
    sweep.record_created_artifacts("add_weigh_in_with_timestamps", "{}", ctx)

    assert ctx.created_workout_ids == [801, 802]
    assert ctx.created_course_ids == [901]
    assert ctx.food_id == "food-901"
    assert ctx.serving_id == "serving-902"
    sweep.record_created_artifacts("add_weigh_in", "{}", ctx)

    assert ctx.created_weigh_in_dates == ["2026-05-17"]


def test_collect_food_log_ids_finds_sweep_named_entries():
    class Client:
        def connectapi(self, url):
            assert url == "/nutrition-service/food/logs/2026-05-17"
            return {
                "meals": [
                    {
                        "items": [
                            {
                                "logId": 1001,
                                "name": "garmin mcp live sweep quick add 20260517120000",
                            },
                            {"logId": 1002, "name": "unrelated food"},
                        ]
                    }
                ]
            }

    ctx = complete_context()
    ctx.created_food_log_ids.clear()

    sweep.collect_food_log_ids(Client(), ctx)

    assert ctx.created_food_log_ids == [1001]


def test_collect_food_log_ids_ignores_stale_same_day_sweep_entries():
    class Client:
        def connectapi(self, url):
            assert url == "/nutrition-service/food/logs/2026-05-17"
            return {
                "meals": [
                    {
                        "items": [
                            {
                                "logId": 1001,
                                "name": "garmin mcp live sweep quick add 20260517110000",
                            },
                            {
                                "logId": 1002,
                                "name": "garmin mcp live sweep quick add 20260517120000",
                            },
                        ]
                    }
                ]
            }

    ctx = complete_context()
    ctx.created_food_log_ids.clear()

    sweep.collect_food_log_ids(Client(), ctx)

    assert ctx.created_food_log_ids == [1002]


def test_record_created_artifacts_cleans_up_blood_pressure_without_version():
    class Client:
        def __init__(self):
            self.deleted = []

        def get_blood_pressure(self, start_date, end_date):
            assert start_date == "2026-05-17"
            assert end_date == "2026-05-17"
            return {
                "measurementSummaries": [
                    {
                        "measurements": [
                            {
                                "version": 123,
                                "measurementTimestampLocal": "2026-05-17T12:00:00.000",
                            }
                        ]
                    }
                ]
            }

        def delete_blood_pressure(self, version, cdate):
            self.deleted.append((version, cdate))
            return {}

    ctx = complete_context()
    client = Client()

    sweep.record_created_artifacts(
        "set_blood_pressure",
        json.dumps(
            {
                "version": None,
                "measurementTimestampLocal": "2026-05-17T12:00:00.000",
            }
        ),
        ctx,
        client,
    )

    assert client.deleted == [("123", "2026-05-17")]


def test_content_to_text_extracts_fastmcp_call_tool_tuple():
    text = sweep.content_to_text(
        (
            [
                SimpleNamespace(
                    text="Error retrieving custom foods: HTTP error: API Error 403"
                )
            ],
            {
                "result": (
                    "Error retrieving custom foods: "
                    "HTTP error: API Error 403"
                )
            },
        )
    )

    assert text == "Error retrieving custom foods: HTTP error: API Error 403"
    assert sweep.classify(text) == "ERROR"


def test_failed_json_status_is_an_error():
    text = json.dumps(
        {
            "status": "failed",
            "http_status": 500,
            "message": "Failed to delete workout",
        }
    )

    assert sweep.classify(text) == "ERROR"


def test_batch_failure_counts_are_errors():
    text = json.dumps(
        {
            "total": 2,
            "succeeded": 1,
            "failed": 1,
            "results": [
                {"workout_id": 1, "status": "success"},
                {"workout_id": 2, "status": "failed"},
            ],
        }
    )

    assert sweep.classify(text) == "ERROR"


def test_batch_result_error_status_is_an_error_even_without_failed_count():
    text = json.dumps(
        {
            "results": [
                {"workout_id": 1, "status": "success"},
                {"workout_id": 2, "status": "error"},
            ],
        }
    )

    assert sweep.classify(text) == "ERROR"


def test_not_found_json_status_is_no_data():
    text = json.dumps(
        {
            "status": "not_found",
            "http_status": 404,
            "message": "Food log not found",
        }
    )

    assert sweep.classify(text) == "NO_DATA"
