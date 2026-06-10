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
from backend.services.goal_generation_resources import validate_daily_goal_output  # noqa: E402
from backend.services.today_goal_service import get_or_generate_today_goal  # noqa: E402
from backend.services.workday_policy import is_workday  # noqa: E402
from evals.scripts.score_utils import (  # noqa: E402
    case_result,
    has_deliverable,
    has_multi_goal_marker,
    has_vague_text,
    load_cases,
    write_result,
)


def run() -> dict[str, Any]:
    cases = load_cases("daily_goal_cases.json")
    results = []
    for case in cases:
        results.append(_run_case(case))
    return write_result("daily_goal", results)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    day = date.fromisoformat(case["input"]["date"])
    if not is_workday(day):
        hard = [] if "non_workday_skip" in case["expected"]["must"] else ["unexpected_non_workday"]
        return case_result(
            case["id"],
            "daily_goal",
            100 if not hard else 0,
            hard,
            [f"{day.isoformat()} is non-workday; no goal generated."],
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        _seed_context(db_path, case)
        result = get_or_generate_today_goal(db_path, day)
        goal_output = result.goal["goal_output"]
        snapshot = result.goal["daily_goal"]["context_snapshot"]

    hard: list[str] = []
    evidence: list[str] = []
    score = 100
    try:
        validate_daily_goal_output(goal_output)
        evidence.append("daily goal schema passed")
    except Exception as exc:  # noqa: BLE001 - eval should preserve evidence
        hard.append(f"schema_failed:{exc}")
        score -= 40

    main_goal = goal_output.get("main_goal", "")
    if has_multi_goal_marker(main_goal):
        hard.append("multi_goal")
        score -= 25
    if has_vague_text(main_goal) and not has_deliverable(main_goal):
        hard.append("vague_goal")
        score -= 25
    if not has_deliverable(" ".join([main_goal, goal_output.get("minimum_acceptable_result", "")])):
        hard.append("missing_deliverable")
        score -= 15
    if len(goal_output.get("completion_criteria") or []) < 2:
        hard.append("missing_criteria")
        score -= 20
    if not goal_output.get("minimum_acceptable_result") or not goal_output.get("stretch_challenge"):
        hard.append("missing_minimum_or_stretch")
        score -= 15
    if not goal_output.get("do_not_do_today"):
        score -= 5

    expected = case["expected"]["must"]
    if "uses_weekly_focus" in expected and goal_output["context_used"]["primary_driver"] != "last_week_focus":
        hard.append("weekly_focus_not_used")
        score -= 25
    if "selected_focus_snapshot" in expected and not snapshot.get("selected_weekly_focus_id"):
        hard.append("selected_focus_missing")
        score -= 20
    if "uses_tomorrow_direction" in expected and not snapshot.get("tomorrow_direction"):
        hard.append("tomorrow_direction_missing")
        score -= 15
    if "time_bounded" in expected and goal_output["estimated_minutes"] > max(90, int(case["input"].get("minutes", 90))):
        hard.append("time_bound_ignored")
        score -= 15

    evidence.append(f"main_goal={main_goal}")
    evidence.append(f"primary_driver={goal_output['context_used']['primary_driver']}")
    return case_result(case["id"], "daily_goal", score, hard, evidence, "收紧 Goal Generator 或 Goal Critic 规则。")


def _seed_context(db_path: Path, case: dict[str, Any]) -> None:
    day = date.fromisoformat(case["input"]["date"])
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful personal daily-goal and weekly-review agent.",
                current_focus_projects=[case["input"].get("project", "DayPilot MVP")],
                default_available_minutes=int(case["input"].get("minutes", 80)),
            )
            repo.create_ability_state(
                connection,
                state_date=day.isoformat(),
                current_difficulty=float(case["input"].get("difficulty", 3)),
                target_difficulty_level=int(case["input"].get("difficulty", 3)),
                recent_completion_rate=0.75,
                recent_felt_difficulty_avg=3.0,
                default_estimated_minutes=int(case["input"].get("minutes", 80)),
                preferred_goal_type_weights={"coding": 0.6, "documentation": 0.2, "testing": 0.2},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["vague goals", "multi goal"],
                adjustment_direction="hold",
                update_reason="Eval seed.",
                is_current=1,
            )
            if case["input"].get("tomorrow_direction"):
                _seed_previous_checkin(connection, day, case["input"]["tomorrow_direction"])
            if case["input"].get("weekly_focus"):
                _seed_weekly_focus(connection, day, case["input"]["weekly_focus"])
    finally:
        connection.close()


def _seed_previous_checkin(connection, day: date, tomorrow_direction: str) -> None:
    previous = day - timedelta(days=3 if day.isoweekday() == 1 else 1)
    previous_goal_id = repo.create_daily_goal(
        connection,
        goal_date=previous.isoformat(),
        context_snapshot={"source": "daily-goal-eval"},
        generated_at=f"{previous.isoformat()} 09:00:00",
    )
    repo.create_goal_version(
        connection,
        daily_goal_id=previous_goal_id,
        version_no=1,
        is_active=1,
        main_goal="完成上一工作日的 DayPilot 小切片",
        goal_reason="Eval seed.",
        success_criteria=["交付一个小切片", "记录验收结果"],
        estimated_minutes=60,
        difficulty_level=3,
        minimum_version="留下可检查记录。",
        goal_type="coding",
        revision_source="initial_generation",
    )
    repo.create_daily_checkin(
        connection,
        daily_goal_id=previous_goal_id,
        checkin_date=previous.isoformat(),
        week_id=repo.week_id_for_date(previous),
        completion_text="完成上一工作日目标。",
        felt_difficulty=3,
        tomorrow_direction=tomorrow_direction,
        parsed_completion_rate=1.0,
        completed_items=["上一工作日目标"],
        unfinished_items=[],
        blockers=[],
        actual_outputs=["eval artifact"],
        processor_snapshot={"source": "daily-goal-eval"},
    )


def _seed_weekly_focus(connection, day: date, focus_text: str) -> None:
    source_week_start = day - timedelta(days=7)
    weekly_report_id = repo.create_weekly_report(
        connection,
        week_id=repo.week_id_for_date(source_week_start),
        week_start_date=source_week_start.isoformat(),
        week_end_date=(source_week_start + timedelta(days=4)).isoformat(),
        generated_on_date=(source_week_start + timedelta(days=4)).isoformat(),
        completed_work="- Eval seed completed work.",
        next_week_plan=f"- {focus_text}。",
        weekly_reflection="- Eval seed reflection.",
        report_text="本周完成工作\n- Eval seed completed work.",
        source_snapshot={"source": "daily-goal-eval"},
    )
    repo.create_weekly_focus(
        connection,
        weekly_report_id=weekly_report_id,
        source_week_id=repo.week_id_for_date(source_week_start),
        target_week_id=repo.week_id_for_date(day),
        focus_order=1,
        focus_text=focus_text,
        desired_outcome=f"{focus_text}形成可验证结果。",
        focus_type="testing" if "测试" in focus_text or "评估" in focus_text else "coding",
        priority=5,
        status="active",
        context_payload={"source": ["eval"]},
    )


def main() -> None:
    summary = run()
    print(f"daily_goal: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
