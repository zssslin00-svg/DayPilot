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


def test_soul_project_import_adds_updates_renames_and_preserves_frontend_active_projects() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import.sqlite3"
        soul_path = root / "SOUL.md"
        _seed_db(db_path)

        _write_soul(
            soul_path,
            """
1. P0 Alpha 项目：当前进度：新的 Alpha 进度。项目最终目标：确认 Alpha 最小闭环。项目今日目标：整理 Alpha 验收清单。
2. P1 Beta 项目：当前进度：刚开始。项目最终目标：写出 Beta 方案。项目今日目标：写出 Beta 第一版结构。
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
        assert "整理 Alpha 验收清单" in repo.project_today_goal(alpha)
        assert beta is not None
        assert beta["priority"] == "P1"
        assert repo.project_target_goal(beta) == "写出 Beta 方案"
        assert "写出 Beta 第一版结构" in repo.project_today_goal(beta)
        assert len(goals) == 2

        repeated = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload
        assert repeated["status"] == "no_change"
        assert _event_count(db_path) == events_after_first

        _write_soul(
            soul_path,
            """
1. P0 Alpha 改名后项目：当前进度：新的 Alpha 进度。项目最终目标：确认 Alpha 最小闭环。项目今日目标：整理 Alpha 验收清单。
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
1. P0 Alpha 改名后项目：当前进度：SOUL 继续推进。
""",
        )
        completed = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload
        assert completed["status"] == "applied"
        assert completed["direction"] == "soul_to_frontend"
        assert completed["soul_patched_count"] == 1
        assert completed["updated_count"] == 1
        assert completed["completed_count"] == 0

        connection = initialize_database(db_path)
        try:
            alpha_after = repo.get_project(connection, 1)
            beta_after = repo.get_project_by_name(connection, "Beta 项目")
            active_names = [project["name"] for project in repo.list_projects(connection)]
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()
        assert alpha_after["status_summary"] == "SOUL 继续推进"
        assert beta_after["status"] == "active"
        assert active_names == ["Alpha 改名后项目", "Beta 项目"]
        assert profile["current_focus_projects"] == ["Alpha 改名后项目", "Beta 项目"]
        assert "Beta 项目" in soul_path.read_text(encoding="utf-8")


def test_soul_project_import_no_active_projects_clears_profile_projects() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-empty.sqlite3"
        soul_path = root / "SOUL.md"
        _seed_db(db_path)

        _write_soul(soul_path, "暂无 active 项目。")
        result = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert result["status"] == "applied"
        assert result["direction"] == "soul_to_frontend"
        assert result["soul_patched_count"] == 1
        assert result["completed_count"] == 0

        connection = initialize_database(db_path)
        try:
            active_projects = repo.list_projects(connection)
            alpha = repo.get_project(connection, 1)
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()

        assert [project["name"] for project in active_projects] == ["Alpha 项目"]
        assert alpha["status"] == "active"
        assert profile["current_focus_projects"] == ["Alpha 项目"]
        assert "Alpha 项目" in soul_path.read_text(encoding="utf-8")


def test_soul_project_import_legacy_goal_backfills_final_and_today_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-legacy.sqlite3"
        soul_path = root / "SOUL.md"

        _write_soul(
            soul_path,
            "1. P0 Legacy 项目：当前进度：只有旧格式。目标：确认旧格式仍能导入。",
        )
        result = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert result["status"] == "applied"
        connection = initialize_database(db_path)
        try:
            project = repo.get_project_by_name(connection, "Legacy 项目")
        finally:
            connection.close()

        assert project is not None
        assert repo.project_target_goal(project) == "确认旧格式仍能导入"
        assert "确认旧格式仍能导入" in repo.project_today_goal(project)


def test_soul_project_import_accepts_non_list_priority_lines() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-loose.sqlite3"
        soul_path = root / "SOUL.md"

        _write_soul(
            soul_path,
            "P0 Loose 项目 当前进度：还在整理。最终目标：形成固定导入格式。今日目标：跑通非列表导入。",
        )
        result = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert result["status"] == "applied"
        connection = initialize_database(db_path)
        try:
            project = repo.get_project_by_name(connection, "Loose 项目")
        finally:
            connection.close()

        assert project is not None
        assert repo.project_target_goal(project) == "形成固定导入格式"
        assert "跑通非列表导入" in repo.project_today_goal(project)


def test_soul_project_import_llm_fallback_parses_prose_section() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-prose.sqlite3"
        soul_path = root / "SOUL.md"

        _write_soul(
            soul_path,
            "我现在主要推进 Gamma 项目，当前进度是刚完成调研，最终目标是形成可复用导入器，今日目标是整理解析样例。",
        )
        result = import_current_projects_from_soul(db_path, soul_path=soul_path, today=WORKDAY).payload

        assert result["status"] == "applied"
        connection = initialize_database(db_path)
        try:
            project = repo.get_project_by_name(connection, "Gamma 项目")
        finally:
            connection.close()

        assert project is not None
        assert repo.project_target_goal(project) == "形成可复用导入器"
        assert "整理解析样例" in repo.project_today_goal(project)


def main() -> None:
    test_soul_project_import_adds_updates_renames_and_preserves_frontend_active_projects()
    test_soul_project_import_no_active_projects_clears_profile_projects()
    test_soul_project_import_legacy_goal_backfills_final_and_today_goal()
    test_soul_project_import_accepts_non_list_priority_lines()
    test_soul_project_import_llm_fallback_parses_prose_section()
    print("PASS: SOUL.md current project import adds, updates, renames, and preserves frontend active projects")


if __name__ == "__main__":
    main()
