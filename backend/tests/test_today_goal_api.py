from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.api.server import (  # noqa: E402
    NON_WORKDAY_MESSAGE,
    WORKDAY_GENERATED_GOAL_MESSAGE,
    create_server,
)
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import connect_database, initialize_database  # noqa: E402
from backend.services.goal_generation_resources import validate_daily_goal_output  # noqa: E402


class FakeDeepSeekResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeDeepSeekResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _deepseek_payload(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "response-1",
        "model": "deepseek-v4-pro",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, ensure_ascii=False),
                }
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_today_goal(today: date, db_path: Path) -> tuple[int, dict[str, Any]]:
    return _request_today("GET", "/api/today-goal", today, db_path)


def _post_regenerate_today_goal(today: date, db_path: Path) -> tuple[int, dict[str, Any]]:
    return _request_today("POST", "/api/today-goal/regenerate", today, db_path)


def _request_today(method: str, path: str, today: date, db_path: Path) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
        soul_path=db_path.parent / "missing-SOUL.md",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            method=method,
        )
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_weekend_today_goal_skips_generation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "missing.sqlite3"

        status, payload = _get_today_goal(date(2026, 6, 13), db_path)

        assert status == 200
        assert payload == {
            "date": "2026-06-13",
            "is_workday": False,
            "message": NON_WORKDAY_MESSAGE,
            "goal": None,
        }
        assert not db_path.exists()


def test_china_holiday_today_goal_skips_generation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "holiday.sqlite3"

        status, payload = _get_today_goal(date(2026, 1, 1), db_path)

        assert status == 200
        assert payload["date"] == "2026-01-01"
        assert payload["is_workday"] is False
        assert payload["goal"] is None
        assert not db_path.exists()


def test_china_makeup_weekend_today_goal_generates() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "makeup-weekend.sqlite3"

        status, payload = _get_today_goal(date(2026, 1, 4), db_path)

        assert status == 200
        assert payload["date"] == "2026-01-04"
        assert payload["is_workday"] is True
        assert payload["created"] is True
        assert payload["goal"]["daily_goal"]["weekday"] == 7
        assert payload["goal"]["daily_goal"]["is_workday"] == 1


def test_workday_today_goal_generates_and_persists_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-test.sqlite3"

        status, payload = _get_today_goal(date(2026, 6, 8), db_path)

        assert status == 200
        assert payload["date"] == "2026-06-08"
        assert payload["is_workday"] is True
        assert payload["created"] is True
        assert payload["message"] == WORKDAY_GENERATED_GOAL_MESSAGE
        assert payload["goal"]["daily_goal"]["goal_date"] == "2026-06-08"
        assert payload["goal"]["active_version"]["revision_source"] == "initial_generation"
        assert payload["goal"]["active_version"]["critic_result"]["quality_status"] == "passed"
        validate_daily_goal_output(payload["goal"]["goal_output"])
        assert db_path.exists()

        first_daily_goal_id = payload["goal"]["daily_goal"]["id"]
        first_active_version_id = payload["goal"]["active_version"]["id"]

        second_status, second_payload = _get_today_goal(date(2026, 6, 8), db_path)

        assert second_status == 200
        assert second_payload["created"] is False
        assert second_payload["goal"]["daily_goal"]["id"] == first_daily_goal_id
        assert second_payload["goal"]["active_version"]["id"] == first_active_version_id

        connection = connect_database(db_path)
        try:
            daily_goal_count = connection.execute("SELECT COUNT(*) FROM daily_goals").fetchone()[0]
            version_count = connection.execute("SELECT COUNT(*) FROM goal_versions").fetchone()[0]
            assert daily_goal_count == 1
            assert version_count == 1
        finally:
            connection.close()


def test_workday_today_goal_normalizes_deepseek_schema_shape_without_mock_fallback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-deepseek-normalized.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a reliable daily goal parser.",
                    current_focus_projects=["DayPilot Parser"],
                    default_available_minutes=90,
                )
        finally:
            connection.close()

        raw_goal = {
            "schema_version": "daily_goal.v1",
            "goal_date": "2026-06-08",
            "main_goal": "Repair DayPilot schema parser fallback path",
            "rationale": "A valid DeepSeek goal with minor shape issues should be normalized instead of falling back to mock.",
            "completion_criteria": [
                "Normalize invalid growth tags before schema validation",
                "Persist the normalized DeepSeek goal output",
            ],
            "estimated_minutes": "200",
            "difficulty": "3.0",
            "minimum_acceptable_result": "One schema validation path succeeds without mock fallback.",
            "stretch_challenge": "Add one regression record for this parser repair.",
            "do_not_do_today": "不要扩展到周报生成",
            "goal_type": "implementation",
            "growth_tags": ["数据构建方案", "规则标注"],
            "context_used": {
                "project_priority": "P0",
                "weekly_focus_alignment": "not_applicable",
            },
        }

        original_urlopen = urllib.request.urlopen
        original_env = {
            "DAYPILOT_LLM_MODE": os.environ.get("DAYPILOT_LLM_MODE"),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
            "DAYPILOT_LLM_LOG_ENABLED": os.environ.get("DAYPILOT_LLM_LOG_ENABLED"),
        }
        try:
            os.environ["DAYPILOT_LLM_MODE"] = "deepseek"
            os.environ["DEEPSEEK_API_KEY"] = "test-key"
            os.environ["DAYPILOT_LLM_LOG_ENABLED"] = "0"
            urllib.request.urlopen = lambda *args, **kwargs: FakeDeepSeekResponse(  # type: ignore[assignment]
                _deepseek_payload(raw_goal)
            )

            status, payload = _get_today_goal(date(2026, 6, 8), db_path)
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        assert status == 200
        goal_output = payload["goal"]["goal_output"]
        validate_daily_goal_output(goal_output)
        assert goal_output["growth_tags"] == ["daypilot_mvp", "daily_goal", "coding"]
        assert goal_output["context_used"]["primary_driver"] == "current_project"
        assert goal_output["do_not_do_today"] == ["不要扩展到周报生成"]
        assert payload["goal"]["daily_goal"]["context_snapshot"]["llm_mode"] == "deepseek"
        assert payload["goal"]["daily_goal"]["context_snapshot"]["fallback_reason"] is None


def test_regenerate_today_goal_replaces_existing_mock_goal_with_deepseek_version() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-regenerate.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a real LLM daily goal flow.",
                    current_focus_projects=["DayPilot Parser"],
                    default_available_minutes=90,
                )
                repo.create_project(
                    connection,
                    id=1,
                    name="DayPilot Parser",
                    status="active",
                    status_summary="Old mock goal should be replaced.",
                    planning_bias="Prefer parser fixes.",
                )
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-08",
                    context_snapshot={
                        "llm_metadata": {
                            "llm_mode_used": "mock",
                            "fallback_reason": "old_mock_fallback",
                        },
                        "fallback_reason": "old_mock_fallback",
                        "goal_output_context_used": {
                            "primary_driver": "current_project",
                            "tomorrow_direction_handling": "empty_agent_decided",
                            "continuity_note": "Old mock context.",
                            "difficulty_reason": "Old mock difficulty.",
                        },
                    },
                    generated_at="2026-06-08 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Old mock goal",
                    goal_reason="Old mock rationale.",
                    success_criteria=["Old criterion one", "Old criterion two"],
                    estimated_minutes=60,
                    difficulty_level=2,
                    minimum_version="Old mock minimum result.",
                    stretch_challenge="Old mock stretch challenge.",
                    avoid_today=json.dumps(["Old mock avoid"], ensure_ascii=False),
                    goal_type="coding",
                    revision_source="initial_generation",
                    critic_result={
                        "llm_metadata": {
                            "llm_mode_used": "mock",
                            "fallback_reason": "old_mock_fallback",
                        }
                    },
                    prompt_version="goal_generation_v1_mock",
                )
        finally:
            connection.close()

        raw_goal = {
            "schema_version": "daily_goal.v1",
            "goal_date": "2026-06-08",
            "main_goal": "交付 DayPilot API schema 解析修复记录",
            "rationale": "重新生成应调用 DeepSeek，并用真实模型返回替换旧 mock active version。",
            "completion_criteria": [
                "保存 DeepSeek 返回的目标版本",
                "确认旧 mock fallback 不再是 active version",
            ],
            "estimated_minutes": 75,
            "difficulty": 3,
            "minimum_acceptable_result": "保存一条 DeepSeek 生成的可检查目标版本。",
            "stretch_challenge": "补充一条验证记录说明 active version 已切换。",
            "do_not_do_today": ["不要扩展到周报或项目生命周期"],
            "goal_type": "coding",
            "growth_tags": ["daypilot_mvp", "daily_goal", "structured_output"],
            "context_used": {
                "primary_driver": "current_project",
                "tomorrow_direction_handling": "empty_agent_decided",
                "continuity_note": "手动刷新要求重新调用模型生成今日目标。",
                "difficulty_reason": "目标限制在解析链路修复记录，预计 75 分钟完成。",
            },
        }

        original_urlopen = urllib.request.urlopen
        original_env = {
            "DAYPILOT_LLM_MODE": os.environ.get("DAYPILOT_LLM_MODE"),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
            "DAYPILOT_LLM_LOG_ENABLED": os.environ.get("DAYPILOT_LLM_LOG_ENABLED"),
        }
        try:
            os.environ["DAYPILOT_LLM_MODE"] = "deepseek"
            os.environ["DEEPSEEK_API_KEY"] = "test-key"
            os.environ["DAYPILOT_LLM_LOG_ENABLED"] = "0"
            urllib.request.urlopen = lambda *args, **kwargs: FakeDeepSeekResponse(  # type: ignore[assignment]
                _deepseek_payload(raw_goal)
            )

            status, payload = _post_regenerate_today_goal(date(2026, 6, 8), db_path)
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        assert status == 200
        assert payload["created"] is True
        assert payload["created_count"] == 1
        active_version = payload["goal"]["active_version"]
        assert active_version["main_goal"] == raw_goal["main_goal"]
        assert active_version["revision_source"] == "system_regeneration"
        assert active_version["prompt_version"] == "goal_generation_v3_deepseek"
        assert active_version["critic_result"]["llm_metadata"]["llm_mode_used"] == "deepseek"
        assert active_version["critic_result"]["llm_metadata"]["fallback_reason"] is None
        assert payload["goal"]["daily_goal"]["context_snapshot"]["fallback_reason"] is None
        validate_daily_goal_output(payload["goal"]["goal_output"])


def test_workday_today_goal_reads_existing_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-test.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a useful daily goal loop.",
                )
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date="2026-06-08",
                    context_snapshot={"source": "test"},
                    generated_at="2026-06-08 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Implement workday policy.",
                    goal_reason="Weekend skipping belongs in one service.",
                    success_criteria=["Monday-Friday allowed", "Weekend skipped"],
                    estimated_minutes=45,
                    difficulty_level=2,
                    minimum_version="Policy and API tests pass.",
                    goal_type="implementation",
                    revision_source="initial_generation",
                )
        finally:
            connection.close()

        status, payload = _get_today_goal(date(2026, 6, 8), db_path)

        assert status == 200
        assert payload["is_workday"] is True
        assert payload["created"] is False
        assert payload["goal"]["daily_goal"]["goal_date"] == "2026-06-08"
        assert payload["goal"]["daily_goal"]["is_workday"] == 1
        assert payload["goal"]["active_version"]["main_goal"] == "Implement workday policy."
        assert payload["goal"]["daily_checkin"] is None
        validate_daily_goal_output(payload["goal"]["goal_output"])


def test_workday_today_goal_includes_existing_checkin() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checked-in-test.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a useful daily goal loop.",
                )
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date="2026-06-08",
                    context_snapshot={"source": "test"},
                    generated_at="2026-06-08 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Return check-in state with the today goal.",
                    goal_reason="The frontend should not guess from goal status.",
                    success_criteria=["Payload includes daily_checkin"],
                    estimated_minutes=45,
                    difficulty_level=2,
                    minimum_version="Today API returns the persisted check-in.",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date="2026-06-08",
                    week_id="2026-W24",
                    completion_text="Submitted the real check-in.",
                    felt_difficulty=2,
                    parsed_completion_rate=1.0,
                    completed_items=["check-in state"],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=["today API payload"],
                    processor_snapshot={"source": "test"},
                    created_at="2026-06-08 18:00:00",
                )
        finally:
            connection.close()

        status, payload = _get_today_goal(date(2026, 6, 8), db_path)

        assert status == 200
        assert payload["created"] is False
        assert payload["goal"]["daily_goal"]["status"] == "checked_in"
        assert payload["goal"]["daily_checkin"]["daily_goal_id"] == daily_goal_id
        assert payload["goal"]["daily_checkin"]["completion_text"] == "Submitted the real check-in."


def test_workday_today_goal_repairs_stale_checked_in_status_without_checkin() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-stale-checkin-status.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a useful daily goal loop.",
                )
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date="2026-06-08",
                    status="checked_in",
                    checked_in_at="2026-06-08 18:00:00",
                    context_snapshot={"source": "test"},
                    generated_at="2026-06-08 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Do not hide goals without a check-in row.",
                    goal_reason="The actual check-in table is the source of truth.",
                    success_criteria=["Stale status is repaired"],
                    estimated_minutes=45,
                    difficulty_level=2,
                    minimum_version="Today API shows the goal as active.",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
        finally:
            connection.close()

        status, payload = _get_today_goal(date(2026, 6, 8), db_path)

        assert status == 200
        assert payload["created"] is False
        assert payload["goal"]["daily_goal"]["status"] == "active"
        assert payload["goal"]["daily_goal"]["checked_in_at"] is None
        assert payload["goal"]["daily_checkin"] is None

        connection = connect_database(db_path)
        try:
            repaired_goal = repo.get_daily_goal(connection, daily_goal_id)
            assert repaired_goal["status"] == "active"
            assert repaired_goal["checked_in_at"] is None
        finally:
            connection.close()


def test_workday_generation_reads_required_context() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-context-test.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build a useful daily goal loop.",
                    current_focus_projects=["DayPilot MVP"],
                    default_available_minutes=80,
                )
                repo.create_project(
                    connection,
                    id=1,
                    name="DayPilot MVP",
                    status="active",
                    status_summary="Context reader work is in progress.",
                    planning_bias="Prefer backend work.",
                )
                previous_goal_id = repo.create_daily_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-08",
                    context_snapshot={"source": "test"},
                    generated_at="2026-06-08 09:00:00",
                )
                previous_version_id = repo.create_goal_version(
                    connection,
                    daily_goal_id=previous_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Create the repository context readers.",
                    goal_reason="The generator needs recent records before it can choose a goal.",
                    success_criteria=["Read recent goals", "Read recent check-ins"],
                    estimated_minutes=60,
                    difficulty_level=2,
                    minimum_version="Context readers exist.",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                feedback_id = repo.create_feedback_message(
                    connection,
                    daily_goal_id=previous_goal_id,
                    before_version_id=previous_version_id,
                    raw_message="Tomorrow I want to keep this backend-focused.",
                    feedback_type="short_term_preference",
                    affected_scope="next_3_7_days",
                    interpretation_json={"summary": "Prefer backend work"},
                    extracted_preferences={"prefer": ["backend"]},
                    memory_action="update_short_term_preference",
                    should_regenerate_goal=0,
                    is_resolved=1,
                )
                checkin_id = repo.create_daily_checkin(
                    connection,
                    daily_goal_id=previous_goal_id,
                    checkin_date="2026-06-08",
                    week_id="2026-W24",
                    completion_text="Finished the context readers.",
                    felt_difficulty=2,
                    tomorrow_direction="继续打通后端今日目标生成",
                    parsed_completion_rate=1.0,
                    completed_items=["context readers"],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=["backend repositories"],
                    processor_snapshot={"confidence": 0.9},
                )
                ability_state_id = repo.create_ability_state(
                    connection,
                    state_date="2026-06-08",
                    current_difficulty=3.0,
                    target_difficulty_level=3,
                    recent_completion_rate=1.0,
                    recent_felt_difficulty_avg=2.0,
                    default_estimated_minutes=80,
                    preferred_goal_type_weights={"coding": 1.0},
                    short_term_preferences={"prefer": ["backend"]},
                    long_term_preferences_snapshot={},
                    avoid_patterns_snapshot=["vague goals"],
                    adjustment_direction="hold",
                    update_reason="Recent completion is high and difficulty feels light.",
                    is_current=1,
                )
                weekly_report_id = repo.create_weekly_report(
                    connection,
                    week_id="2026-W23",
                    week_start_date="2026-06-01",
                    week_end_date="2026-06-05",
                    generated_on_date="2026-06-05",
                    completed_work="- Built persistence foundations.",
                    next_week_plan="- Connect the goal service to persistence.",
                    weekly_reflection="- Keep the MVP scoped.",
                    report_text="本周完成工作\n- Built persistence foundations.",
                    source_snapshot={"daily_goal_ids": [previous_goal_id]},
                )
                weekly_focus_id = repo.create_weekly_focus(
                    connection,
                    weekly_report_id=weekly_report_id,
                    source_week_id="2026-W23",
                    target_week_id="2026-W24",
                    focus_order=1,
                    focus_text="Connect the goal service to persistence",
                    desired_outcome="The today-goal API can create and reuse persisted goals.",
                    focus_type="coding",
                    priority=5,
                    context_payload={"source": "test"},
                )
        finally:
            connection.close()

        status, payload = _get_today_goal(date(2026, 6, 9), db_path)

        assert status == 200
        assert payload["created"] is True
        snapshot = payload["goal"]["daily_goal"]["context_snapshot"]
        assert snapshot["profile_id"] == 1
        assert snapshot["ability_state_id"] == ability_state_id
        assert snapshot["recent_daily_goal_ids"] == [previous_goal_id]
        assert snapshot["recent_checkin_ids"] == [checkin_id]
        assert snapshot["recent_feedback_message_ids"] == [feedback_id]
        assert snapshot["weekly_focus_ids"] == [weekly_focus_id]
        assert snapshot["project_ids"] == [1]
        assert snapshot["tomorrow_direction"] == "继续打通后端今日目标生成"
        assert payload["goal"]["goal_output"]["context_used"]["primary_driver"] == "last_week_focus"
        validate_daily_goal_output(payload["goal"]["goal_output"])


def test_multi_project_generation_and_next_day_carryover() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-multi-project.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="Build project-scoped daily loops.",
                    current_focus_projects=["Alpha project", "Beta project"],
                    default_available_minutes=80,
                )
        finally:
            connection.close()

        first_status, first_payload = _get_today_goal(date(2026, 6, 8), db_path)
        assert first_status == 200
        assert first_payload["active_project_count"] == 2
        assert len(first_payload["goals"]) == 2
        assert first_payload["created_count"] == 2

        connection = initialize_database(db_path)
        try:
            with connection:
                goals = first_payload["goals"]
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=goals[0]["daily_goal"]["id"],
                    checkin_date="2026-06-08",
                    week_id="2026-W24",
                    completion_status="completed",
                    completion_text="Completed Alpha project goal.",
                    felt_difficulty=2,
                    parsed_completion_rate=1.0,
                    completed_items=["alpha"],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=["alpha output"],
                    processor_snapshot={"source": "test"},
                )
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=goals[1]["daily_goal"]["id"],
                    checkin_date="2026-06-08",
                    week_id="2026-W24",
                    completion_status="incomplete",
                    completion_text="Beta project goal is not finished.",
                    felt_difficulty=4,
                    parsed_completion_rate=0.3,
                    completed_items=[],
                    unfinished_items=["beta"],
                    blockers=[],
                    actual_outputs=[],
                    processor_snapshot={"source": "test"},
                )
        finally:
            connection.close()

        second_status, second_payload = _get_today_goal(date(2026, 6, 9), db_path)
        assert second_status == 200
        assert len(second_payload["goals"]) == 2
        assert second_payload["created_count"] == 1
        assert second_payload["carried_over_count"] == 1
        snapshots = [goal["daily_goal"]["context_snapshot"] for goal in second_payload["goals"]]
        assert sum(1 for snapshot in snapshots if snapshot.get("carryover_from_goal_id")) == 1
        carried_goals = [
            goal
            for goal in second_payload["goals"]
            if goal["daily_goal"]["context_snapshot"].get("carryover_from_goal_id")
        ]
        assert len(carried_goals) == 1
        carried_output = carried_goals[0]["goal_output"]
        assert carried_output["main_goal"].startswith("继续完成")
        assert "Beta project" in carried_output["main_goal"]
        assert carried_output["context_used"]["primary_driver"] == "recent_unfinished_work"


def main() -> None:
    test_weekend_today_goal_skips_generation()
    test_china_holiday_today_goal_skips_generation()
    test_china_makeup_weekend_today_goal_generates()
    test_workday_today_goal_generates_and_persists_goal()
    test_workday_today_goal_normalizes_deepseek_schema_shape_without_mock_fallback()
    test_regenerate_today_goal_replaces_existing_mock_goal_with_deepseek_version()
    test_workday_today_goal_reads_existing_goal()
    test_workday_today_goal_includes_existing_checkin()
    test_workday_today_goal_repairs_stale_checked_in_status_without_checkin()
    test_workday_generation_reads_required_context()
    test_multi_project_generation_and_next_day_carryover()
    print("PASS: GET /api/today-goal generates, persists, reuses, and reads required context")


if __name__ == "__main__":
    main()
