from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config.settings import DayPilotSettings  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import connect_database, initialize_database  # noqa: E402
from backend.services.project_progress_service import (  # noqa: E402
    ensure_projects_seeded,
    update_project_progress_for_checkin,
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
        "id": "project-progress-response",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _seed_db(db_path: Path, completion_text: str | None = None) -> int:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a flexible personal work system.",
                goal_preferences={
                    "project_priorities": [
                        {
                            "id": 5,
                            "name": "验证 DayPilot 是否能融入真实日常",
                            "priority": "P0",
                            "role": "主线",
                            "progress": "项目已经写完，正在看效果是否满足日常生活需求。",
                            "planning_bias": "优先安排真实使用和反馈闭环。",
                        },
                        {
                            "id": 2,
                            "name": "提升规则召回模型的召回率、全面性",
                            "priority": "P0",
                            "role": "主线",
                            "progress": "已有初版，但召回率和全面性不足。",
                        },
                    ],
                },
            )
            goal_id = repo.create_daily_goal(
                connection,
                goal_date="2026-06-08",
                context_snapshot={"source": "test"},
                generated_at="2026-06-08 09:00:00",
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=goal_id,
                version_no=1,
                is_active=1,
                main_goal="验证 DayPilot 的真实日用 check-in 流程。",
                goal_reason="项目需要确认是否能融入真实日常。",
                success_criteria=["保存 check-in", "观察前端是否够简洁"],
                estimated_minutes=60,
                difficulty_level=2,
                minimum_version="完成一次真实 check-in。",
                goal_type="testing",
                revision_source="initial_generation",
            )
            checkin_id = repo.create_daily_checkin(
                connection,
                daily_goal_id=goal_id,
                checkin_date="2026-06-08",
                week_id="2026-W24",
                completion_text=completion_text
                or "今天真实使用了 DayPilot，完成 check-in 并发现前端提示需要更精简。",
                felt_difficulty=2,
                tomorrow_direction="继续验证 DayPilot 的日常使用效果。",
                parsed_completion_rate=1.0,
                completed_items=["DayPilot check-in"],
                unfinished_items=[],
                blockers=[],
                actual_outputs=["真实使用记录"],
                processor_snapshot={"source": "test"},
            )
            return checkin_id
    finally:
        connection.close()


def test_projects_seed_from_profile_priorities() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-projects.sqlite3"
        _seed_db(db_path)
        connection = connect_database(db_path)
        try:
            with connection:
                projects = ensure_projects_seeded(connection)
        finally:
            connection.close()

        by_id = {project["id"]: project for project in projects}
        assert set(by_id) == {2, 5}
        assert by_id[5]["priority"] == "P0"
        assert "日常生活需求" in by_id[5]["status_summary"]


def test_mock_update_writes_event_and_updates_summary() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-project-progress.sqlite3"
        checkin_id = _seed_db(db_path)

        result = update_project_progress_for_checkin(
            db_path,
            checkin_id,
            settings=_settings(mode="mock"),
        ).payload

        assert result["status"] == "updated"
        assert result["project_id"] == 5
        assert result["confidence"] == 0.62

        connection = connect_database(db_path)
        try:
            project = repo.get_project(connection, 5)
            events = repo.list_project_progress_events_for_source(
                connection,
                "daily_checkin",
                checkin_id,
                active_only=True,
            )
        finally:
            connection.close()

        assert project is not None
        assert "前端提示需要更精简" in project["status_summary"]
        assert len(events) == 1
        assert events[0]["applied_to_summary"] == 1


def test_low_confidence_deepseek_output_still_updates_summary() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-project-progress-low-confidence.sqlite3"
        checkin_id = _seed_db(db_path)
        output = {
            "project_id": 2,
            "confidence": 0.08,
            "progress_delta": "记录了规则召回模型的新实验方向。",
            "new_status_summary": "已有初版，正在准备覆盖 6 个规则库的微调验证。",
            "evidence_text": "召回模型需要覆盖 6 个规则库。",
            "reason": "用户明确提到规则召回模型。",
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = update_project_progress_for_checkin(
                db_path,
                checkin_id,
                settings=_settings(mode="deepseek"),
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "updated"
        assert result["project_id"] == 2
        assert result["confidence"] == 0.08
        assert result["llm_mode_used"] == "deepseek"

        connection = connect_database(db_path)
        try:
            project = repo.get_project(connection, 2)
        finally:
            connection.close()

        assert project is not None
        assert project["status_summary"] == output["new_status_summary"]


def test_invalid_deepseek_output_falls_back_to_mock_update() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-project-progress-fallback.sqlite3"
        checkin_id = _seed_db(db_path)
        original = urllib.request.urlopen
        try:
            bad_output = {
                "project_id": 999,
                "confidence": 0.7,
                "progress_delta": "bad",
                "new_status_summary": "bad",
                "evidence_text": "bad",
                "reason": "bad project id",
            }
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(bad_output, ensure_ascii=False))
            )
            result = update_project_progress_for_checkin(
                db_path,
                checkin_id,
                settings=_settings(mode="deepseek"),
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "updated"
        assert "initial_failure=invalid_project_id" in result["fallback_reason"]
        assert "repair_failure=invalid_project_id" in result["fallback_reason"]
        assert result["llm_mode_used"] == "mock"


def test_editing_same_checkin_supersedes_old_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-project-progress-edit.sqlite3"
        checkin_id = _seed_db(db_path)
        first = update_project_progress_for_checkin(
            db_path,
            checkin_id,
            settings=_settings(mode="mock"),
        ).payload

        connection = connect_database(db_path)
        try:
            with connection:
                repo.update_daily_checkin(
                    connection,
                    checkin_id,
                    completion_text="今天改为推进规则召回模型，整理了 6 个规则库的覆盖问题。",
                    tomorrow_direction="继续做规则召回模型的数据准备。",
                )
        finally:
            connection.close()

        second = update_project_progress_for_checkin(
            db_path,
            checkin_id,
            settings=_settings(mode="mock"),
        ).payload

        connection = connect_database(db_path)
        try:
            all_events = repo.list_project_progress_events_for_source(
                connection,
                "daily_checkin",
                checkin_id,
            )
            active_events = repo.list_project_progress_events_for_source(
                connection,
                "daily_checkin",
                checkin_id,
                active_only=True,
            )
        finally:
            connection.close()

        assert first["status"] == "updated"
        assert second["status"] == "updated"
        assert len(all_events) == 2
        assert len(active_events) == 1
        assert all_events[0]["event_status"] == "superseded"
        assert active_events[0]["project_id"] == 2


def main() -> None:
    test_projects_seed_from_profile_priorities()
    test_mock_update_writes_event_and_updates_summary()
    test_low_confidence_deepseek_output_still_updates_summary()
    test_invalid_deepseek_output_falls_back_to_mock_update()
    test_editing_same_checkin_supersedes_old_event()
    print("PASS: project progress seeding, DeepSeek routing, fallback, and edits verified")


if __name__ == "__main__":
    main()
