from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.soul_project_import_service import import_current_projects_from_soul  # noqa: E402


WORKDAY = date(2026, 6, 9)


def _write_soul(path: Path, current_projects: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 长期方向",
                "",
                "项目驱动成长。",
                "",
                "## 当前项目",
                "",
                current_projects.strip(),
                "",
                "每日生成规则：",
                "",
                "- 每个 active 项目都生成一个今日目标。",
                "",
                "## 用户偏好",
                "",
                "- 小目标。",
            ]
        ),
        encoding="utf-8",
    )


def _seed_db(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily loop.",
                current_focus_projects=["Alpha 项目"],
                goal_preferences={"project_priorities": []},
            )
            repo.create_project(
                connection,
                id=1,
                name="Alpha 项目",
                priority="P0",
                role="主线",
                status="active",
                status_summary="旧 Alpha 进度。",
                planning_bias="旧 Alpha 规划。",
                source_payload={},
            )
    finally:
        connection.close()


def _event_count(db_path: Path) -> int:
    connection = initialize_database(db_path)
    try:
        return len(repo.list_recent_project_lifecycle_events(connection, limit=50))
    finally:
        connection.close()


def test_soul_project_import_adds_updates_renames_completes_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import.sqlite3"
        soul_path = root / "SOUL.md"
        _seed_db(db_path)

        _write_soul(
            soul_path,
            """
1. P0 Alpha 项目：当前进度：新的 Alpha 进度。目标：确认 Alpha 最小闭环。
2. P1 Beta 项目：当前进度：刚开始。目标：写出 Beta 方案。
""",
        )
        first = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert first["status"] == "applied"
        assert first["updated_count"] == 1
        assert first["created_count"] == 1
        assert first["renamed_count"] == 0
        assert first["completed_count"] == 0

        connection = initialize_database(db_path)
        try:
            alpha = repo.get_project(connection, 1)
            beta = repo.get_project_by_name(connection, "Beta 项目")
            goals = repo.list_daily_goals_by_date(connection, WORKDAY.isoformat())
            events_after_first = len(repo.list_recent_project_lifecycle_events(connection, limit=50))
        finally:
            connection.close()

        assert alpha["status_summary"] == "新的 Alpha 进度"
        assert repo.project_target_goal(alpha) == "确认 Alpha 最小闭环"
        assert beta is not None
        assert beta["priority"] == "P1"
        assert repo.project_target_goal(beta) == "写出 Beta 方案"
        assert len(goals) == 2

        repeated = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload
        assert repeated["status"] == "no_change"
        assert _event_count(db_path) == events_after_first

        _write_soul(
            soul_path,
            """
1. P0 Alpha 改名后项目：当前进度：新的 Alpha 进度。目标：确认 Alpha 最小闭环。
2. P1 Beta 项目。
""",
        )
        renamed = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload
        assert renamed["status"] == "applied"
        assert renamed["renamed_count"] == 1

        connection = initialize_database(db_path)
        try:
            renamed_alpha = repo.get_project(connection, 1)
            old_alpha = repo.get_project_by_name(connection, "Alpha 项目")
        finally:
            connection.close()
        assert renamed_alpha["name"] == "Alpha 改名后项目"
        assert old_alpha is None

        _write_soul(
            soul_path,
            """
1. P0 Alpha 改名后项目。
""",
        )
        completed = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload
        assert completed["status"] == "applied"
        assert completed["completed_count"] == 1

        connection = initialize_database(db_path)
        try:
            beta_after = repo.get_project_by_name(connection, "Beta 项目")
            active_names = [project["name"] for project in repo.list_projects(connection)]
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()
        assert beta_after["status"] == "completed"
        assert active_names == ["Alpha 改名后项目"]
        assert profile["current_focus_projects"] == ["Alpha 改名后项目"]


def test_soul_project_import_no_active_projects_clears_profile_projects() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-empty.sqlite3"
        soul_path = root / "SOUL.md"
        _seed_db(db_path)

        _write_soul(soul_path, "暂无 active 项目。")
        result = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert result["status"] == "applied"
        assert result["completed_count"] == 1

        connection = initialize_database(db_path)
        try:
            active_projects = repo.list_projects(connection)
            alpha = repo.get_project(connection, 1)
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()

        assert active_projects == []
        assert alpha["status"] == "completed"
        assert profile["current_focus_projects"] == []
        assert profile["goal_preferences"]["project_priorities"] == []


def main() -> None:
    test_soul_project_import_adds_updates_renames_completes_and_is_idempotent()
    test_soul_project_import_no_active_projects_clears_profile_projects()
    print("PASS: SOUL.md current project import adds, updates, renames, completes, and stays idempotent")


if __name__ == "__main__":
    main()
