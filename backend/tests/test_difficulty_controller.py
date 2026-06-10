from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.difficulty_controller import (  # noqa: E402
    parse_completion_rate,
    update_ability_state_after_checkin,
)


def test_completion_text_parser() -> None:
    assert parse_completion_rate("完成了 70%").completion_rate == 0.7
    assert parse_completion_rate("基本完成，剩一点收尾").completion_rate == 0.8
    assert parse_completion_rate("完成一半").completion_rate == 0.5
    assert parse_completion_rate("完全没做").completion_rate == 0.0
    assert parse_completion_rate("没有完成，但定位到问题").completion_rate == 0.25


def test_consecutive_easy_completion_increases_difficulty() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "difficulty-up.sqlite3")
        try:
            with connection:
                _seed_profile(connection)
                _seed_ability(connection, "2026-06-10", difficulty=2)
                _seed_goal_and_checkin(connection, "2026-06-08", parsed_rate=1.0, felt=2)
                _seed_goal_and_checkin(connection, "2026-06-09", parsed_rate=0.9, felt=2)
                checkin_id = _seed_goal_and_checkin(
                    connection,
                    "2026-06-10",
                    completion_text="完成了 95%",
                    felt=2,
                )

                result = update_ability_state_after_checkin(connection, checkin_id)

                assert result.ability_state["target_difficulty_level"] == 3
                assert result.ability_state["adjustment_direction"] == "increase"
                assert "high_completion_easy_3d" in result.difficulty_update_event["reason_codes"]
        finally:
            connection.close()


def test_low_completion_and_hard_feeling_decreases_difficulty() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "difficulty-down.sqlite3")
        try:
            with connection:
                _seed_profile(connection)
                _seed_ability(connection, "2026-06-09", difficulty=3)
                _seed_goal_and_checkin(connection, "2026-06-08", parsed_rate=0.4, felt=4)
                checkin_id = _seed_goal_and_checkin(
                    connection,
                    "2026-06-09",
                    completion_text="完全没做",
                    felt=5,
                )

                result = update_ability_state_after_checkin(connection, checkin_id)

                assert result.ability_state["target_difficulty_level"] == 2
                assert result.ability_state["adjustment_direction"] == "decrease"
                assert result.difficulty_update_event["delta"] == -1
        finally:
            connection.close()


def test_high_completion_but_hard_holds_difficulty() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "difficulty-hold.sqlite3")
        try:
            with connection:
                _seed_profile(connection)
                _seed_ability(connection, "2026-06-08", difficulty=3)
                checkin_id = _seed_goal_and_checkin(
                    connection,
                    "2026-06-08",
                    completion_text="完成了 100%",
                    felt=4,
                )

                result = update_ability_state_after_checkin(connection, checkin_id)

                assert result.ability_state["target_difficulty_level"] == 3
                assert "high_completion_but_hard" in result.difficulty_update_event["reason_codes"]
        finally:
            connection.close()


def test_low_completion_but_easy_marks_direction_mismatch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "difficulty-direction.sqlite3")
        try:
            with connection:
                _seed_profile(connection)
                _seed_ability(connection, "2026-06-08", difficulty=3)
                checkin_id = _seed_goal_and_checkin(
                    connection,
                    "2026-06-08",
                    completion_text="完成了 20%",
                    felt=2,
                )

                result = update_ability_state_after_checkin(connection, checkin_id)

                assert result.ability_state["target_difficulty_level"] == 3
                assert result.ability_state["adjustment_direction"] == "change_direction"
                assert "low_completion_easy_direction_mismatch" in result.difficulty_update_event["reason_codes"]
        finally:
            connection.close()


def test_difficulty_boundary_does_not_go_below_one() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        connection = initialize_database(Path(temp_dir) / "difficulty-boundary.sqlite3")
        try:
            with connection:
                _seed_profile(connection)
                _seed_ability(connection, "2026-06-08", difficulty=1)
                checkin_id = _seed_goal_and_checkin(
                    connection,
                    "2026-06-08",
                    completion_text="完全没做",
                    felt=5,
                )

                result = update_ability_state_after_checkin(connection, checkin_id)

                assert result.ability_state["target_difficulty_level"] == 1
                assert result.difficulty_update_event["delta"] == 0
        finally:
            connection.close()


def _seed_profile(connection) -> None:
    repo.create_user_profile(
        connection,
        id=1,
        long_term_direction="Build a useful daily goal loop.",
    )


def _seed_ability(connection, state_date: str, *, difficulty: int) -> None:
    repo.create_ability_state(
        connection,
        state_date=state_date,
        current_difficulty=float(difficulty),
        target_difficulty_level=difficulty,
        recent_completion_rate=None,
        recent_felt_difficulty_avg=None,
        default_estimated_minutes=60,
        preferred_goal_type_weights={"coding": 1.0},
        short_term_preferences={},
        long_term_preferences_snapshot={},
        avoid_patterns_snapshot=[],
        adjustment_direction="initial",
        update_reason="Seed state.",
        is_current=1,
    )


def _seed_goal_and_checkin(
    connection,
    checkin_date: str,
    *,
    completion_text: str = "完成了目标。",
    parsed_rate: float | None = None,
    felt: int = 3,
) -> int:
    daily_goal_id = repo.create_daily_goal(
        connection,
        goal_date=checkin_date,
        context_snapshot={"source": "test"},
        generated_at=f"{checkin_date} 09:00:00",
    )
    repo.create_goal_version(
        connection,
        daily_goal_id=daily_goal_id,
        version_no=1,
        is_active=1,
        main_goal="Ship a narrow backend improvement.",
        goal_reason="Difficulty tests need a current active version.",
        success_criteria=["Save a check-in", "Update ability state"],
        estimated_minutes=60,
        difficulty_level=2,
        minimum_version="The check-in can be saved.",
        goal_type="coding",
        revision_source="initial_generation",
    )
    return repo.create_daily_checkin(
        connection,
        daily_goal_id=daily_goal_id,
        checkin_date=checkin_date,
        week_id=repo.week_id_for_date(checkin_date),
        completion_text=completion_text,
        felt_difficulty=felt,
        tomorrow_direction=None,
        parsed_completion_rate=parsed_rate,
        completed_items=[],
        unfinished_items=[],
        blockers=[],
        actual_outputs=[],
        processor_snapshot={},
    )


def main() -> None:
    test_completion_text_parser()
    test_consecutive_easy_completion_increases_difficulty()
    test_low_completion_and_hard_feeling_decreases_difficulty()
    test_high_completion_but_hard_holds_difficulty()
    test_low_completion_but_easy_marks_direction_mismatch()
    test_difficulty_boundary_does_not_go_below_one()
    print("PASS: difficulty controller parses completion text and updates ability state")


if __name__ == "__main__":
    main()
