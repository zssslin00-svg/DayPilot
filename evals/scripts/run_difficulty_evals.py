from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.difficulty_controller import update_ability_state_after_checkin  # noqa: E402
from evals.scripts.score_utils import case_result, load_cases, write_result  # noqa: E402


BASE_DATE = date(2026, 3, 2)


def run() -> dict[str, Any]:
    cases = load_cases("difficulty_cases.json")
    results = [_run_case(index, case) for index, case in enumerate(cases)]
    return write_result("difficulty", results)


def _run_case(index: int, case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_user_profile(connection, id=1, long_term_direction="Build DayPilot MVP.")
                seed_difficulty = 2 if case["expected_direction"] == "increase" else 3
                repo.create_ability_state(
                    connection,
                    state_date=(BASE_DATE - timedelta(days=1)).isoformat(),
                    current_difficulty=float(seed_difficulty),
                    target_difficulty_level=seed_difficulty,
                    recent_completion_rate=0.75,
                    recent_felt_difficulty_avg=3.0,
                    default_estimated_minutes=80,
                    preferred_goal_type_weights={"coding": 1.0},
                    short_term_preferences={},
                    long_term_preferences_snapshot={},
                    avoid_patterns_snapshot=[],
                    adjustment_direction="hold",
                    update_reason="Eval seed.",
                    is_current=1,
                )
                if case["expected_direction"] == "increase":
                    _seed_previous_checkin(connection, BASE_DATE - timedelta(days=2), 0.95, 2)
                    _seed_previous_checkin(connection, BASE_DATE - timedelta(days=1), 0.95, 2)
                checkin_id = _seed_current_checkin(connection, BASE_DATE + timedelta(days=index), case)
                result = update_ability_state_after_checkin(connection, checkin_id)
        finally:
            connection.close()

    actual = result.ability_state["adjustment_direction"]
    hard = [] if actual == case["expected_direction"] else [f"expected_{case['expected_direction']}_got_{actual}"]
    score = 100 if not hard else 0
    evidence = [
        f"completion_rate={result.completion_parse_result['completion_rate']}",
        f"felt_difficulty={case['felt_difficulty']}",
        f"reason={result.ability_state['update_reason']}",
    ]
    return case_result(case["id"], "difficulty", score, hard, evidence, "调整 Difficulty Controller 决策矩阵。")


def _seed_previous_checkin(connection, day: date, rate: float, felt: int) -> None:
    goal_id = _seed_goal(connection, day)
    repo.create_daily_checkin(
        connection,
        daily_goal_id=goal_id,
        checkin_date=day.isoformat(),
        week_id=repo.week_id_for_date(day),
        completion_text="完成历史目标。",
        felt_difficulty=felt,
        parsed_completion_rate=rate,
        completed_items=["历史目标"],
        unfinished_items=[],
        blockers=[],
        actual_outputs=["history"],
        processor_snapshot={"source": "difficulty-eval"},
    )


def _seed_current_checkin(connection, day: date, case: dict[str, Any]) -> int:
    goal_id = _seed_goal(connection, day)
    return repo.create_daily_checkin(
        connection,
        daily_goal_id=goal_id,
        checkin_date=day.isoformat(),
        week_id=repo.week_id_for_date(day),
        completion_text=case["completion_text"],
        felt_difficulty=case["felt_difficulty"],
        tomorrow_direction=None,
        parsed_completion_rate=None,
        completed_items=[],
        unfinished_items=[],
        blockers=[],
        actual_outputs=[],
        processor_snapshot={"source": "difficulty-eval"},
    )


def _seed_goal(connection, day: date) -> int:
    goal_id = repo.create_daily_goal(
        connection,
        goal_date=day.isoformat(),
        context_snapshot={"source": "difficulty-eval"},
        generated_at=f"{day.isoformat()} 09:00:00",
    )
    repo.create_goal_version(
        connection,
        daily_goal_id=goal_id,
        version_no=1,
        is_active=1,
        main_goal="完成一个难度评估切片",
        goal_reason="Eval seed.",
        success_criteria=["交付切片", "记录结果"],
        estimated_minutes=60,
        difficulty_level=3,
        minimum_version="留下记录。",
        goal_type="coding",
        revision_source="initial_generation",
    )
    return goal_id


def main() -> None:
    summary = run()
    print(f"difficulty: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
