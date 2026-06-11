from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config.settings import DayPilotSettings  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.project_lifecycle_service import (  # noqa: E402
    apply_project_lifecycle_message,
    get_project_overview,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _settings(*, mode: str = "mock", key: str | None = "test-key") -> DayPilotSettings:
    return DayPilotSettings(
        llm_mode=mode,
        deepseek_api_key=key,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=3,
        deepseek_max_tokens=300,
        deepseek_thinking="disabled",
    )


def _deepseek_payload(content: str) -> dict[str, Any]:
    return {
        "id": "project-lifecycle-response",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                "旧项目段落",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付。",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _seed_db(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily goal loop.",
                current_focus_projects=["验证 DayPilot 是否能融入真实日常"],
                goal_preferences={
                    "project_priorities": [
                        {
                            "id": 5,
                            "name": "验证 DayPilot 是否能融入真实日常",
                            "priority": "P0",
                            "role": "主线",
                            "progress": "项目已经写完，正在看效果是否满足日常生活需求。",
                            "planning_bias": "优先安排真实使用、发现问题、修复日用阻塞、留下试用记录或决策记录。",
                        }
                    ],
                    "priority_policy": {"order": ["P0"]},
                },
            )
            repo.create_project(
                connection,
                id=5,
                name="验证 DayPilot 是否能融入真实日常",
                priority="P0",
                role="主线",
                status="active",
                status_summary="项目已经写完，正在看效果是否满足日常生活需求。",
                planning_bias="优先安排真实使用、发现问题、修复日用阻塞、留下试用记录或决策记录。",
                source_payload={"id": 5},
            )
    finally:
        connection.close()


def _seed_active_goal(connection: sqlite3.Connection, *, project_id: int, goal_date: str, main_goal: str) -> int:
    daily_goal_id = repo.create_daily_goal(
        connection,
        profile_id=1,
        project_id=project_id,
        goal_date=goal_date,
        status="active",
        context_snapshot={},
        generated_at=f"{goal_date} 09:00:00",
    )
    repo.create_goal_version(
        connection,
        daily_goal_id=daily_goal_id,
        version_no=1,
        is_active=1,
        main_goal=main_goal,
        goal_reason="Seeded test goal.",
        success_criteria=["First criterion", "Second criterion"],
        estimated_minutes=60,
        difficulty_level=2,
        minimum_version="Seeded minimum result.",
        stretch_challenge="Seeded stretch.",
        avoid_today=json.dumps(["Seeded avoid"], ensure_ascii=False),
        goal_type="coding",
        revision_source="initial_generation",
        critic_result={},
        prompt_version="goal_generation_v1_mock",
    )
    return daily_goal_id


def _goal_version_count(connection: sqlite3.Connection, *, project_id: int, goal_date: str) -> int:
    record = repo.get_goal_with_active_version_by_date_and_project(connection, goal_date, project_id)
    if record is None:
        return 0
    return len(repo.list_goal_versions(connection, int(record["daily_goal"]["id"])))


def test_deepseek_create_project_updates_db_profile_soul_and_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        output = {
            "action": "create_project",
            "project_name": "微调一个编排规则的模型",
            "project_id": None,
            "priority": "P0",
            "status_summary": "还没确定实现方案、数据集结构",
            "planning_bias": "优先安排实现方案比较、数据集结构设计、最小样例和可验证实验设计。",
            "target_goal": "形成可复查的规则编排微调方案",
            "today_goal": "先确定方案和数据结构",
            "completion_summary": "",
            "confidence": 0.2,
            "reason": "用户明确要求新增 P0 项目。",
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = apply_project_lifecycle_message(
                db_path,
                {"message": "新增 P0 项目：微调一个编排规则的模型。"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "applied"
        assert result["action"] == "create_project"
        assert result["project"]["priority"] == "P0"
        assert result["project"]["status"] == "active"

        connection = initialize_database(db_path)
        try:
            project = repo.get_project_by_name(connection, "微调一个编排规则的模型")
            profile = repo.get_user_profile(connection)
            events = repo.list_recent_project_lifecycle_events(connection, limit=5)
        finally:
            connection.close()

        assert project is not None
        assert project["status_summary"] == "还没确定实现方案、数据集结构"
        assert repo.project_target_goal(project) == "形成可复查的规则编排微调方案"
        assert repo.project_today_goal(project) == "先确定方案和数据结构"
        assert any(item["name"] == "微调一个编排规则的模型" for item in profile["goal_preferences"]["project_priorities"])
        assert "微调一个编排规则的模型" in profile["current_focus_projects"]
        assert events[0]["action"] == "create_project"
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "微调一个编排规则的模型" in soul_text
        assert "当前进度：还没确定实现方案、数据集结构" in soul_text
        assert "项目最终目标：形成可复查的规则编排微调方案" in soul_text
        assert "项目今日目标：先确定方案和数据结构" in soul_text
        assert "## 用户偏好" in soul_text


def test_complete_project_marks_completed_and_hides_from_active_context() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-complete.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)

        result = apply_project_lifecycle_message(
            db_path,
            {"message": "验证 DayPilot 是否能融入真实日常已经完成了，结果是可以进入一周试用。"},
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["action"] == "complete_project"
        assert result["project"]["status"] == "completed"

        overview = get_project_overview(db_path)
        assert overview["active_projects"] == []
        assert overview["completed_projects"][0]["name"] == "验证 DayPilot 是否能融入真实日常"

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            project = repo.get_project(connection, 5)
        finally:
            connection.close()

        assert project["status"] == "completed"
        assert profile["current_focus_projects"] == []
        assert profile["goal_preferences"]["project_priorities"] == []
        assert "验证 DayPilot 是否能融入真实日常" not in soul_path.read_text(encoding="utf-8")


def test_deepseek_update_project_renames_project_and_syncs_context() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-rename.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        output = {
            "action": "update_project",
            "project_id": 5,
            "project_name": "DayPilot 一周真实试用",
            "priority": "P0",
            "status_summary": "正在确认真实日用阻塞",
            "planning_bias": "优先安排真实使用、发现问题、修复日用阻塞、留下试用记录或决策记录。",
            "target_goal": "",
            "completion_summary": "",
            "confidence": 0.9,
            "reason": "用户要求重命名项目并更新进度。",
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = apply_project_lifecycle_message(
                db_path,
                {
                    "message": "把这个项目验证 DayPilot 是否能融入真实日常改成下面这个项目：DayPilot 一周真实试用；当前进度是正在确认真实日用阻塞",
                },
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "applied"
        assert result["action"] == "update_project"
        assert result["project"]["id"] == 5
        assert result["project"]["name"] == "DayPilot 一周真实试用"
        assert result["project"]["status_summary"] == "正在确认真实日用阻塞"

        connection = initialize_database(db_path)
        try:
            renamed = repo.get_project(connection, 5)
            old = repo.get_project_by_name(connection, "验证 DayPilot 是否能融入真实日常")
            profile = repo.get_user_profile(connection)
            event = repo.list_recent_project_lifecycle_events(connection, limit=1)[0]
        finally:
            connection.close()

        assert old is None
        assert renamed["name"] == "DayPilot 一周真实试用"
        assert renamed["source_payload"]["name"] == "DayPilot 一周真实试用"
        assert profile["current_focus_projects"] == ["DayPilot 一周真实试用"]
        assert profile["goal_preferences"]["project_priorities"][0]["name"] == "DayPilot 一周真实试用"
        assert event["action"] == "update_project"
        assert event["project_id"] == 5
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "DayPilot 一周真实试用" in soul_text
        assert "验证 DayPilot 是否能融入真实日常。" not in soul_text


def test_mock_update_project_progress_keeps_existing_name() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-progress.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)

        result = apply_project_lifecycle_message(
            db_path,
            {"message": "更新项目 验证 DayPilot 是否能融入真实日常 当前进度是正在整理真实使用阻塞"},
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["action"] == "update_project"
        assert result["project"]["name"] == "验证 DayPilot 是否能融入真实日常"
        assert result["project"]["status_summary"] == "正在整理真实使用阻塞"


def test_delete_project_hard_deletes_project_and_cascades_history_but_keeps_event_audit() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-delete.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)

        connection = initialize_database(db_path)
        try:
            with connection:
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    profile_id=1,
                    project_id=5,
                    goal_date="2026-06-09",
                    status="active",
                    context_snapshot={},
                )
                checkin_id = repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date="2026-06-09",
                    week_id="2026-W24",
                    completion_text="完成真实使用验证。",
                    felt_difficulty=2,
                    parsed_completion_rate=1.0,
                    completed_items=["真实使用验证"],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=["试用记录"],
                    processor_snapshot={},
                )
                repo.create_project_progress_event(
                    connection,
                    project_id=5,
                    event_date="2026-06-09",
                    source_type="daily_checkin",
                    source_id=checkin_id,
                    progress_delta="完成真实使用验证。",
                    evidence_text="完成真实使用验证。",
                    confidence=0.8,
                    applied_to_summary=1,
                    previous_status_summary="正在验证日常使用。",
                    new_status_summary="完成真实使用验证。",
                    reason="test seed",
                    llm_metadata={},
                    raw_output={},
                )
        finally:
            connection.close()

        result = apply_project_lifecycle_message(
            db_path,
            {"message": "删除项目：验证 DayPilot 是否能融入真实日常"},
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["action"] == "delete_project"
        assert result["project"]["name"] == "验证 DayPilot 是否能融入真实日常"

        connection = initialize_database(db_path)
        try:
            assert repo.get_project(connection, 5) is None
            assert repo.list_daily_goals_by_date(connection, "2026-06-09") == []
            progress_count = connection.execute("SELECT COUNT(*) AS count FROM project_progress_events").fetchone()[
                "count"
            ]
            event = repo.list_recent_project_lifecycle_events(connection, limit=1)[0]
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()

        assert progress_count == 0
        assert event["action"] == "delete_project"
        assert event["project_id"] is None
        assert event["project_name"] == "验证 DayPilot 是否能融入真实日常"
        assert profile["current_focus_projects"] == []
        assert "验证 DayPilot 是否能融入真实日常" not in soul_path.read_text(encoding="utf-8")


def test_batch_lifecycle_applies_success_items_and_audits_failed_items() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-batch.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        connection = initialize_database(db_path)
        try:
            with connection:
                project_five_name = repo.get_project(connection, 5)["name"]
                repo.create_project(
                    connection,
                    id=6,
                    name="Project Beta",
                    priority="P1",
                    role="推进",
                    status="active",
                    status_summary="Beta old summary.",
                    planning_bias="Beta planning.",
                    source_payload={},
                )
        finally:
            connection.close()

        output = {
            "schema_version": "project_lifecycle_batch.v1",
            "items": [
                {
                    "action": "update_project",
                    "project_id": 5,
                    "project_name": "",
                    "priority": "P0",
                    "status_summary": "Batch progress updated.",
                    "planning_bias": "",
                    "target_goal": "",
                    "completion_summary": "",
                    "today_goal_policy": "refresh",
                    "confidence": 0.9,
                    "reason": "valid progress update",
                },
                {
                    "action": "update_project",
                    "project_id": 6,
                    "project_name": project_five_name,
                    "priority": "P1",
                    "status_summary": "This item should roll back.",
                    "planning_bias": "",
                    "target_goal": "",
                    "completion_summary": "",
                    "today_goal_policy": "refresh",
                    "confidence": 0.8,
                    "reason": "rename conflict",
                },
            ],
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = apply_project_lifecycle_message(
                db_path,
                {"message": "batch update"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "partial"
        assert result["action"] == "batch_project_lifecycle"
        assert result["applied_count"] == 1
        assert result["failed_count"] == 1
        assert result["items"][0]["status"] == "applied"
        assert result["items"][1]["status"] == "failed"
        assert result["items"][1]["reason"] == "project_name_conflict"

        connection = initialize_database(db_path)
        try:
            assert repo.get_project(connection, 5)["status_summary"] == "Batch progress updated."
            assert repo.get_project(connection, 6)["name"] == "Project Beta"
            assert repo.get_project(connection, 6)["status_summary"] == "Beta old summary."
            events = repo.list_recent_project_lifecycle_events(connection, limit=5)
        finally:
            connection.close()

        assert any(event["project_id"] == 5 and event["applied"] == 1 for event in events)
        assert any(event["project_id"] == 6 and event["applied"] == 0 for event in events)


def test_project_lifecycle_refreshes_today_goal_only_for_meaningful_project_state_changes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-goal-refresh.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        connection = initialize_database(db_path)
        try:
            with connection:
                project = repo.get_project(connection, 5)
                repo.create_project(
                    connection,
                    id=6,
                    name="Project Beta",
                    priority="P1",
                    role="推进",
                    status="active",
                    status_summary="Beta summary.",
                    planning_bias="Beta planning.",
                    source_payload={},
                )
                _seed_active_goal(connection, project_id=5, goal_date="2026-06-09", main_goal="Alpha old goal")
                _seed_active_goal(connection, project_id=6, goal_date="2026-06-09", main_goal="Beta old goal")
                previous_summary = project["status_summary"]
                previous_planning_bias = project["planning_bias"]
        finally:
            connection.close()

        original_urlopen = urllib.request.urlopen
        original_mode = os.environ.get("DAYPILOT_LLM_MODE")
        try:
            os.environ["DAYPILOT_LLM_MODE"] = "mock"
            rename_output = {
                "action": "update_project",
                "project_id": 5,
                "project_name": "Project Alpha Renamed",
                "priority": "P0",
                "status_summary": previous_summary,
                "planning_bias": previous_planning_bias,
                "target_goal": "",
                "completion_summary": "",
                "today_goal_policy": "refresh",
                "confidence": 0.9,
                "reason": "pure rename should keep today's goal",
            }
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(rename_output, ensure_ascii=False))
            )
            rename_result = apply_project_lifecycle_message(
                db_path,
                {"message": "rename only"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
                today=date(2026, 6, 9),
            ).payload

            progress_output = {
                "action": "update_project",
                "project_id": 5,
                "project_name": "Project Alpha Renamed",
                "priority": "P0",
                "status_summary": "Confirming implementation plan.",
                "planning_bias": previous_planning_bias,
                "target_goal": "",
                "completion_summary": "",
                "today_goal_policy": "keep",
                "confidence": 0.9,
                "reason": "progress change should refresh today's goal",
            }
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(progress_output, ensure_ascii=False))
            )
            progress_result = apply_project_lifecycle_message(
                db_path,
                {"message": "progress update"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
                today=date(2026, 6, 9),
            ).payload
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
            if original_mode is None:
                os.environ.pop("DAYPILOT_LLM_MODE", None)
            else:
                os.environ["DAYPILOT_LLM_MODE"] = original_mode

        assert rename_result["status"] == "applied"
        assert rename_result["items"][0]["today_goal_policy"] == "refresh"
        assert rename_result["items"][0]["today_goal_refresh"] == "refreshed"
        assert progress_result["status"] == "applied"
        assert progress_result["items"][0]["today_goal_policy"] == "refresh"
        assert progress_result["items"][0]["today_goal_refresh"] == "refreshed"

        connection = initialize_database(db_path)
        try:
            assert _goal_version_count(connection, project_id=5, goal_date="2026-06-09") == 3
            assert _goal_version_count(connection, project_id=6, goal_date="2026-06-09") == 1
            active_goal = repo.get_goal_with_active_version_by_date_and_project(connection, "2026-06-09", 5)
        finally:
            connection.close()

        assert active_goal["project"]["name"] == "Project Alpha Renamed"
        assert active_goal["active_version"]["revision_source"] == "system_regeneration"


def test_rename_project_name_conflict_fails_without_mutating() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-conflict.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_project(
                    connection,
                    id=6,
                    name="DayPilot 一周真实试用",
                    priority="P0",
                    role="主线",
                    status="active",
                    status_summary="另一个项目。",
                    planning_bias="",
                    source_payload={},
                )
        finally:
            connection.close()

        output = {
            "action": "update_project",
            "project_id": 5,
            "project_name": "DayPilot 一周真实试用",
            "priority": "P0",
            "status_summary": "正在确认真实日用阻塞",
            "planning_bias": "",
            "confidence": 0.9,
            "reason": "rename conflict test",
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = apply_project_lifecycle_message(
                db_path,
                {"message": "把验证 DayPilot 是否能融入真实日常改名为 DayPilot 一周真实试用"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "failed"
        assert result["reason"] == "project_name_conflict"
        connection = initialize_database(db_path)
        try:
            assert repo.get_project(connection, 5)["name"] == "验证 DayPilot 是否能融入真实日常"
            event = repo.list_recent_project_lifecycle_events(connection, limit=1)[0]
        finally:
            connection.close()
        assert event["action"] == "update_project"
        assert event["applied"] == 0
        assert event["reason"] == "project_name_conflict"


def test_fallback_create_project_defaults_to_p2_without_priority() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-fallback.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)

        result = apply_project_lifecycle_message(
            db_path,
            {
                "message": "新增项目：整理一个规则评估数据集。当前进度：只有想法。目标：先写数据结构草案。",
            },
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["project"]["priority"] == "P2"
        assert result["project"]["name"] == "整理一个规则评估数据集"
        connection = initialize_database(db_path)
        try:
            project = repo.get_project_by_name(connection, "整理一个规则评估数据集")
        finally:
            connection.close()
        assert repo.project_target_goal(project) == "先写数据结构草案"
        assert repo.project_today_goal(project) == "先写数据结构草案"


def test_invalid_model_output_does_not_mutate_data() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "daypilot-lifecycle-invalid.sqlite3"
        soul_path = _soul_file(root)
        _seed_db(db_path)
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload('{"bad": true}')
            )
            result = apply_project_lifecycle_message(
                db_path,
                {"message": "这是一段无法被 fallback 明确解析的项目闲聊。"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "failed"
        connection = initialize_database(db_path)
        try:
            assert len(repo.list_projects(connection, include_archived=True)) == 1
            assert repo.list_recent_project_lifecycle_events(connection, limit=5) == []
        finally:
            connection.close()
        assert soul_path.read_text(encoding="utf-8").count("旧项目段落") == 1


def test_old_projects_status_check_is_migrated_to_completed() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "old-projects.sqlite3"
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE projects (
                  id INTEGER PRIMARY KEY,
                  name TEXT NOT NULL UNIQUE,
                  priority TEXT NOT NULL DEFAULT 'P2'
                    CHECK (priority IN ('P0', 'P1', 'P2')),
                  role TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'archived')),
                  status_summary TEXT NOT NULL DEFAULT '',
                  planning_bias TEXT NOT NULL DEFAULT '',
                  source_payload TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO projects (id, name, priority, status)
                VALUES (1, 'Old project', 'P0', 'active');
                """
            )
        finally:
            connection.close()

        migrated = initialize_database(db_path)
        try:
            with migrated:
                updated = repo.update_project(migrated, 1, status="completed")
            assert updated["status"] == "completed"
        finally:
            migrated.close()


def test_old_project_lifecycle_action_check_is_migrated_to_delete_project() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "old-lifecycle.sqlite3"
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE projects (
                  id INTEGER PRIMARY KEY,
                  name TEXT NOT NULL UNIQUE,
                  priority TEXT NOT NULL DEFAULT 'P2'
                    CHECK (priority IN ('P0', 'P1', 'P2')),
                  role TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'completed', 'archived')),
                  status_summary TEXT NOT NULL DEFAULT '',
                  planning_bias TEXT NOT NULL DEFAULT '',
                  source_payload TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE project_lifecycle_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_date TEXT NOT NULL DEFAULT (date('now')),
                  raw_message TEXT NOT NULL,
                  action TEXT NOT NULL
                    CHECK (action IN ('create_project', 'complete_project', 'update_project', 'no_change')),
                  project_id INTEGER,
                  project_name TEXT,
                  priority TEXT,
                  previous_status TEXT,
                  new_status TEXT,
                  previous_status_summary TEXT,
                  new_status_summary TEXT,
                  planning_bias TEXT,
                  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
                  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1)),
                  reason TEXT,
                  llm_metadata TEXT NOT NULL DEFAULT '{}',
                  raw_output TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );
                """
            )
        finally:
            connection.close()

        migrated = initialize_database(db_path)
        try:
            with migrated:
                event_id = repo.create_project_lifecycle_event(
                    migrated,
                    raw_message="删除项目：Old project",
                    action="delete_project",
                    project_id=None,
                    project_name="Old project",
                    applied=1,
                    llm_metadata={},
                    raw_output={"action": "delete_project"},
                )
            assert repo.get_project_lifecycle_event(migrated, event_id)["action"] == "delete_project"
        finally:
            migrated.close()


def main() -> None:
    test_deepseek_create_project_updates_db_profile_soul_and_event()
    test_complete_project_marks_completed_and_hides_from_active_context()
    test_deepseek_update_project_renames_project_and_syncs_context()
    test_mock_update_project_progress_keeps_existing_name()
    test_delete_project_hard_deletes_project_and_cascades_history_but_keeps_event_audit()
    test_batch_lifecycle_applies_success_items_and_audits_failed_items()
    test_project_lifecycle_refreshes_today_goal_only_for_meaningful_project_state_changes()
    test_rename_project_name_conflict_fails_without_mutating()
    test_fallback_create_project_defaults_to_p2_without_priority()
    test_invalid_model_output_does_not_mutate_data()
    test_old_projects_status_check_is_migrated_to_completed()
    test_old_project_lifecycle_action_check_is_migrated_to_delete_project()
    print("PASS: project lifecycle create, complete, fallback, failure, and migration verified")


if __name__ == "__main__":
    main()
