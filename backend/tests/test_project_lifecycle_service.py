from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import urllib.request
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
            "target_goal": "先确定方案和数据结构",
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
        assert any(item["name"] == "微调一个编排规则的模型" for item in profile["goal_preferences"]["project_priorities"])
        assert "微调一个编排规则的模型" in profile["current_focus_projects"]
        assert events[0]["action"] == "create_project"
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "微调一个编排规则的模型" in soul_text
        assert "还没确定实现方案" not in soul_text
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


def main() -> None:
    test_deepseek_create_project_updates_db_profile_soul_and_event()
    test_complete_project_marks_completed_and_hides_from_active_context()
    test_fallback_create_project_defaults_to_p2_without_priority()
    test_invalid_model_output_does_not_mutate_data()
    test_old_projects_status_check_is_migrated_to_completed()
    print("PASS: project lifecycle create, complete, fallback, failure, and migration verified")


if __name__ == "__main__":
    main()
