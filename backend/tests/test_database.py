from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.repositories.seed import seed_example_workweek  # noqa: E402


CORE_TABLES = {
    "user_profile",
    "projects",
    "daily_goals",
    "goal_versions",
    "daily_checkins",
    "project_lifecycle_events",
    "feedback_messages",
    "profile_memory_events",
    "soul_sync_retry_jobs",
    "ability_state",
    "weekly_reports",
    "weekly_focus",
    "career_chat_sessions",
    "career_chat_messages",
    "career_profile_update_suggestions",
}


def test_schema_and_repository_crud() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "daypilot-test.sqlite3")
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert CORE_TABLES.issubset(tables)

            with connection:
                profile_id = repo.create_user_profile(
                    connection,
                    id=1,
                    display_name="Test User",
                    long_term_direction="Build a useful daily goal loop.",
                    current_focus_projects=["DayPilot"],
                    goal_preferences={"goal_type_weights": {"implementation": 1.0}},
                    avoid_patterns=["vague goals"],
                    default_available_minutes=90,
                    timezone="Asia/Shanghai",
                    workday_rule={"days": [1, 2, 3, 4, 5]},
                )
                assert profile_id == 1
                assert repo.get_user_profile(connection)["current_focus_projects"] == ["DayPilot"]
                repo.update_user_profile(
                    connection,
                    profile_id,
                    career_profile={"current_skills": ["Python"], "development_intentions": ["AI Agent"]},
                )
                assert repo.get_user_profile(connection)["career_profile"]["current_skills"] == ["Python"]

                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date="2026-06-01",
                    is_workday=1,
                    context_snapshot={"recent_days": 0},
                    generated_at="2026-06-01 09:00:00",
                )
                daily_goal = repo.get_daily_goal(connection, daily_goal_id)
                assert daily_goal["week_id"] == "2026-W23"
                assert daily_goal["weekday"] == 1

                first_version_id = repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal="Create the database schema.",
                    goal_reason="Persistence unlocks the rest of the MVP.",
                    success_criteria=["Create tables", "Enable foreign keys"],
                    estimated_minutes=80,
                    difficulty_level=3,
                    minimum_version="Core tables exist.",
                    goal_type="implementation",
                    revision_source="initial_generation",
                    critic_result={"pass": True},
                )
                assert repo.get_goal_version(connection, first_version_id)["is_active"] == 1

                feedback_id = repo.create_feedback_message(
                    connection,
                    daily_goal_id=daily_goal_id,
                    before_version_id=first_version_id,
                    raw_message="Narrow this to schema and tests.",
                    feedback_type="day_constraint",
                    affected_scope="today",
                    interpretation_json={"summary": "Reduce scope"},
                    extracted_constraints={"reduce_scope": True},
                    extracted_preferences={},
                    memory_action="none",
                    should_regenerate_goal=1,
                    is_resolved=1,
                )
                second_version_id = repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=2,
                    is_active=1,
                    main_goal="Create schema and database tests.",
                    goal_reason="The user asked to narrow the scope.",
                    success_criteria=["Schema exists", "Tests pass"],
                    estimated_minutes=60,
                    difficulty_level=3,
                    minimum_version="Schema and one test script exist.",
                    goal_type="implementation",
                    revision_source="user_feedback",
                    revision_reason="User reduced scope.",
                    feedback_message_id=feedback_id,
                    critic_result={"pass": True},
                )
                repo.update_feedback_message(connection, feedback_id, after_version_id=second_version_id)
                assert repo.get_feedback_message(connection, feedback_id)["after_version_id"] == second_version_id

                checkin_id = repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date="2026-06-01",
                    week_id="2026-W23",
                    completion_text="Finished schema and test coverage.",
                    felt_difficulty=3,
                    tomorrow_direction=None,
                    parsed_completion_rate=1.0,
                    completed_items=["schema", "tests"],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=["scripts/init_db.sql"],
                    processor_snapshot={"confidence": 0.9},
                    created_at="2026-06-01 18:00:00",
                )
                assert repo.get_daily_checkin(connection, checkin_id)["tomorrow_direction"] is None
                assert repo.get_daily_goal(connection, daily_goal_id)["status"] == "checked_in"

                first_state_id = repo.create_ability_state(
                    connection,
                    state_date="2026-06-01",
                    current_difficulty=3.0,
                    target_difficulty_level=3,
                    recent_completion_rate=1.0,
                    recent_felt_difficulty_avg=3.0,
                    preferred_goal_type_weights={"implementation": 1.0},
                    short_term_preferences={},
                    long_term_preferences_snapshot={},
                    avoid_patterns_snapshot=["vague goals"],
                    adjustment_direction="initial",
                    update_reason="Initial state for test.",
                    is_current=1,
                )
                second_state_id = repo.create_ability_state(
                    connection,
                    state_date="2026-06-02",
                    source_checkin_id=checkin_id,
                    source_feedback_message_id=feedback_id,
                    current_difficulty=3.1,
                    target_difficulty_level=3,
                    recent_completion_rate=1.0,
                    recent_felt_difficulty_avg=3.0,
                    completion_streak=1,
                    preferred_goal_type_weights={"implementation": 1.0},
                    short_term_preferences={"prefer": ["database"]},
                    long_term_preferences_snapshot={},
                    avoid_patterns_snapshot=["vague goals"],
                    adjustment_direction="hold",
                    update_reason="Completion rate and felt difficulty are balanced.",
                    is_current=1,
                )
                assert repo.get_ability_state(connection, first_state_id)["is_current"] == 0
                assert repo.get_current_ability_state(connection)["id"] == second_state_id
                repo.update_ability_state(connection, first_state_id, is_current=1)
                assert repo.get_current_ability_state(connection)["id"] == first_state_id

                career_session_id = repo.create_career_chat_session(
                    connection,
                    title="Career planning",
                )
                user_message_id = repo.create_career_chat_message(
                    connection,
                    session_id=career_session_id,
                    role="user",
                    content="I know Python and want to build AI Agent skills.",
                    context_snapshot={"source": "test"},
                )
                assert repo.get_career_chat_message(connection, user_message_id)["role"] == "user"
                assistant_message_id = repo.create_career_chat_message(
                    connection,
                    session_id=career_session_id,
                    role="assistant",
                    content="Build a small Agent evaluation project.",
                    recommendations=[
                        {
                            "title": "Agent eval mini project",
                            "deliverable": "A runnable evaluation note.",
                        }
                    ],
                    profile_update_suggestions=[
                        {
                            "category": "current_skills",
                            "items": ["Python"],
                        }
                    ],
                    context_snapshot={"source": "test"},
                    llm_metadata={"provider": "mock"},
                )
                suggestion_id = repo.create_career_profile_update_suggestion(
                    connection,
                    session_id=career_session_id,
                    message_id=assistant_message_id,
                    category="current_skills",
                    suggestion_payload={
                        "category": "current_skills",
                        "items": ["Python"],
                        "evidence": "User said Python.",
                        "reason": "Skill should inform recommendations.",
                    },
                )
                assert len(repo.list_career_chat_sessions(connection)) == 1
                assert len(repo.list_career_chat_messages(connection, career_session_id)) == 2
                assert repo.get_career_profile_update_suggestion(connection, suggestion_id)["status"] == "pending"
                repo.update_career_profile_update_suggestion(
                    connection,
                    suggestion_id,
                    status="dismissed",
                )
                assert repo.list_pending_career_profile_update_suggestions(connection) == []

                retry_job_id = repo.create_soul_sync_retry_job(
                    connection,
                    job_type="profile_memory",
                    status="pending",
                    source_table="profile_memory_events",
                    source_id=1,
                    payload={"profile_id": 1},
                    last_error="SOUL.md section markers were not found.",
                )
                assert repo.get_soul_sync_retry_job(connection, retry_job_id)["status"] == "pending"
                repo.update_soul_sync_retry_job(connection, retry_job_id, status="failed", attempts=1)
                assert repo.soul_sync_retry_status_counts(connection)["failed"] == 1

                weekly_report_id = repo.create_weekly_report(
                    connection,
                    week_id="2026-W23",
                    week_start_date="2026-06-01",
                    week_end_date="2026-06-05",
                    generated_on_date="2026-06-05",
                    completed_work="- Built database foundation.",
                    next_week_plan="- Connect repository helpers to services.",
                    weekly_reflection="- Keep the persistence layer simple.",
                    report_text="Completed work\n- Built database foundation.",
                    source_snapshot={
                        "daily_goal_ids": [daily_goal_id],
                        "active_version_ids": [second_version_id],
                        "checkin_ids": [checkin_id],
                        "feedback_message_ids": [feedback_id],
                        "ability_state_id": first_state_id,
                    },
                    quality_score=4,
                )
                assert repo.get_weekly_report(connection, weekly_report_id)["quality_score"] == 4

                weekly_focus_id = repo.create_weekly_focus(
                    connection,
                    weekly_report_id=weekly_report_id,
                    source_week_id="2026-W23",
                    target_week_id="2026-W24",
                    focus_order=1,
                    focus_text="Connect persistence to services.",
                    desired_outcome="Service code can read and write daily loop records.",
                    focus_type="implementation",
                    priority=5,
                    context_payload={"must_include": ["service tests"]},
                )
                assert repo.get_weekly_focus(connection, weekly_focus_id)["priority"] == 5

            goal_with_version = repo.get_goal_with_active_version_by_date(connection, "2026-06-01")
            assert goal_with_version["active_version"]["id"] == second_version_id
            assert len(repo.list_feedback_messages_by_date(connection, "2026-06-01")) == 1
        finally:
            connection.close()


def test_example_workweek_seed_and_queries() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "daypilot-seed-test.sqlite3")
        try:
            counts = seed_example_workweek(connection)
            assert counts == {
                "user_profile": 1,
                "daily_goals": 5,
                "goal_versions": 6,
                "daily_checkins": 5,
                "feedback_messages": 1,
                "ability_state": 6,
                "weekly_reports": 1,
                "weekly_focus": 2,
            }

            monday = repo.get_goal_with_active_version_by_date(connection, "2026-06-08")
            assert monday["daily_goal"]["goal_date"] == "2026-06-08"
            assert monday["active_version"]["id"] == 5002

            feedback = repo.list_feedback_messages_by_date(connection, "2026-06-08")
            assert len(feedback) == 1
            assert feedback[0]["feedback_type"] == "day_constraint"

            workweek = repo.get_workweek_records(connection, "2026-W24")
            assert len(workweek) == 5
            assert [record["daily_goal"]["weekday"] for record in workweek] == [1, 2, 3, 4, 5]
            assert all(record["active_version"] is not None for record in workweek)
            assert all(record["daily_checkin"] is not None for record in workweek)

            report = repo.get_weekly_report_by_week(connection, "2026-W24")
            assert report["source_snapshot"]["daily_goal_ids"] == [1001, 1002, 1003, 1004, 1005]

            focus = repo.list_weekly_focus_by_target_week(connection, "2026-W25")
            assert [item["id"] for item in focus] == [8001, 8002]
            assert repo.get_current_ability_state(connection)["id"] == 3005
        finally:
            connection.close()


def test_legacy_project_columns_migrate_to_project_state() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "legacy-project-state.sqlite3"
        legacy = sqlite3.connect(db_path)
        try:
            legacy.executescript(
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
                INSERT INTO projects (
                  id, name, priority, role, status, status_summary, planning_bias, source_payload
                )
                VALUES (
                  7,
                  'Rule orchestration',
                  'P0',
                  'main',
                  'active',
                  'Confirming ruleset orchestration design.',
                  'Prefer data structure and minimal validation tasks.',
                  '{"target_goal":"Deliver flexible rule orchestration.","other":"ignored"}'
                );
                """
            )
        finally:
            legacy.close()

        connection = initialize_database(db_path)
        try:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(projects)").fetchall()
            }
            assert "project_state" in columns
            assert "status_summary" not in columns
            assert "planning_bias" not in columns
            assert "source_payload" not in columns

            project = repo.get_project(connection, 7)
            assert project["name"] == "Rule orchestration"
            assert project["status_summary"] == "Confirming ruleset orchestration design."
            assert project["planning_bias"] == "Prefer data structure and minimal validation tasks."
            assert project["project_state"]["target_goal"] == "Deliver flexible rule orchestration."
        finally:
            connection.close()


def main() -> None:
    test_schema_and_repository_crud()
    test_example_workweek_seed_and_queries()
    test_legacy_project_columns_migrate_to_project_state()
    print("PASS: database schema, repositories, seed data, and required queries verified")


if __name__ == "__main__":
    main()
