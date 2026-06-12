from __future__ import annotations

import json
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
from backend.services.today_goal_service import get_or_generate_today_goal, refresh_today_goal_for_project  # noqa: E402


def _seed_project(connection, *, project_id: int, name: str) -> None:
    repo.create_project(
        connection,
        id=project_id,
        name=name,
        priority="P0" if project_id == 1 else "P1",
        role="main" if project_id == 1 else "support",
        status="active",
        status_summary=f"{name} summary.",
        planning_bias=f"{name} planning.",
        source_payload={
            "target_goal": f"{name} final goal.",
            "today_goal": f"{name} today constraint.",
        },
    )


def _seed_goal(
    connection,
    *,
    project_id: int,
    goal_date: str,
    main_goal: str,
    goal_source: str = "daily_planning",
) -> int:
    daily_goal_id = repo.create_daily_goal(
        connection,
        profile_id=1,
        project_id=project_id,
        goal_date=goal_date,
        goal_source=goal_source,
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
        goal_reason="Seeded goal.",
        success_criteria=["First criterion", "Second criterion"],
        estimated_minutes=60,
        difficulty_level=2,
        minimum_version="Seeded minimum.",
        stretch_challenge="Seeded stretch.",
        avoid_today=json.dumps(["Seeded avoid"], ensure_ascii=False),
        goal_type="coding",
        revision_source="initial_generation",
        critic_result={},
        prompt_version="goal_generation_v1_mock",
    )
    return daily_goal_id


def _version_count(connection, *, project_id: int, goal_date: str) -> int:
    record = repo.get_goal_with_active_version_by_date_and_project(connection, goal_date, project_id)
    if record is None:
        return 0
    return len(repo.list_goal_versions(connection, int(record["daily_goal"]["id"])))


def test_refresh_today_goal_for_project_keeps_or_refreshes_only_target_project() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "today-goal-service.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(connection, id=1, long_term_direction="Build focused daily goals.")
                _seed_project(connection, project_id=1, name="Project Alpha")
                _seed_project(connection, project_id=2, name="Project Beta")
                _seed_goal(connection, project_id=1, goal_date="2026-06-09", main_goal="Alpha old goal")
                _seed_goal(connection, project_id=2, goal_date="2026-06-09", main_goal="Beta old goal")
        finally:
            connection.close()

        kept = refresh_today_goal_for_project(
            db_path,
            date(2026, 6, 9),
            1,
            force=False,
            revision_reason="keep existing project goal",
        )
        refreshed = refresh_today_goal_for_project(
            db_path,
            date(2026, 6, 9),
            1,
            force=True,
            revision_reason="refresh target project goal",
        )

        assert kept.status == "kept"
        assert refreshed.status == "refreshed"
        connection = initialize_database(db_path)
        try:
            assert _version_count(connection, project_id=1, goal_date="2026-06-09") == 2
            assert _version_count(connection, project_id=2, goal_date="2026-06-09") == 1
            active_goal = repo.get_goal_with_active_version_by_date_and_project(connection, "2026-06-09", 1)
        finally:
            connection.close()
        assert active_goal["active_version"]["revision_source"] == "system_regeneration"
        assert "Project Alpha today constraint." in active_goal["active_version"]["main_goal"]
        assert active_goal["daily_goal"]["context_snapshot"]["project_today_goal"] == "Project Alpha today constraint."
        assert repo.project_today_goal(active_goal["project"]) == active_goal["active_version"]["main_goal"]

        refreshed_again = refresh_today_goal_for_project(
            db_path,
            date(2026, 6, 9),
            1,
            force=True,
            revision_reason="refresh target project goal again",
        )
        assert refreshed_again.status == "refreshed"
        connection = initialize_database(db_path)
        try:
            assert _version_count(connection, project_id=1, goal_date="2026-06-09") == 3
            active_goal = repo.get_goal_with_active_version_by_date_and_project(connection, "2026-06-09", 1)
        finally:
            connection.close()
        assert active_goal["active_version"]["main_goal"].count("围绕「Project Alpha」交付：") == 1


def test_refresh_today_goal_for_project_skips_non_workday() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "today-goal-weekend.sqlite3"
        result = refresh_today_goal_for_project(
            db_path,
            date(2026, 6, 13),
            1,
            force=True,
            revision_reason="weekend skip",
        )

        assert result.status == "skipped_non_workday"
        assert not db_path.exists()


def test_today_goal_returns_one_current_goal_per_project() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "today-goal-project-dedupe.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(connection, id=1, long_term_direction="Keep focused.")
                _seed_project(connection, project_id=1, name="Project Alpha")
                planning_goal_id = _seed_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-09",
                    main_goal="Alpha daily planning goal.",
                )
                _seed_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-09",
                    main_goal="Alpha career recommendation goal.",
                    goal_source="career_recommendation",
                )
                _seed_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-09",
                    main_goal="Alpha second career recommendation goal.",
                    goal_source="career_recommendation",
                )
        finally:
            connection.close()

        result = get_or_generate_today_goal(db_path, date(2026, 6, 9))

        assert len(result.goals) == 1
        assert result.goals[0]["daily_goal"]["id"] == planning_goal_id
        assert result.goals[0]["project"]["id"] == 1
        connection = initialize_database(db_path)
        try:
            assert len(repo.list_daily_goals_by_date(connection, "2026-06-09")) == 1
            archived_count = connection.execute(
                "SELECT COUNT(*) AS count FROM daily_goals WHERE goal_date = ? AND status = 'archived'",
                ("2026-06-09",),
            ).fetchone()["count"]
            assert archived_count == 2
        finally:
            connection.close()


def test_today_goal_reuses_existing_career_goal_instead_of_creating_duplicate() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "today-goal-career-only.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(connection, id=1, long_term_direction="Keep focused.")
                _seed_project(connection, project_id=1, name="Project Alpha")
                career_goal_id = _seed_goal(
                    connection,
                    project_id=1,
                    goal_date="2026-06-09",
                    main_goal="Alpha career recommendation goal.",
                    goal_source="career_recommendation",
                )
        finally:
            connection.close()

        result = get_or_generate_today_goal(db_path, date(2026, 6, 9))

        assert len(result.goals) == 1
        assert result.goals[0]["daily_goal"]["id"] == career_goal_id
        connection = initialize_database(db_path)
        try:
            assert len(repo.list_daily_goals_by_date(connection, "2026-06-09")) == 1
        finally:
            connection.close()


def main() -> None:
    test_refresh_today_goal_for_project_keeps_or_refreshes_only_target_project()
    test_refresh_today_goal_for_project_skips_non_workday()
    test_today_goal_returns_one_current_goal_per_project()
    test_today_goal_reuses_existing_career_goal_instead_of_creating_duplicate()
    print("PASS: today goal service project-scoped refresh verified")


if __name__ == "__main__":
    main()
