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
from backend.services.checkin_service import save_daily_checkin  # noqa: E402
from backend.services.today_goal_service import get_or_generate_today_goal  # noqa: E402
from backend.services.weekly_report_service import WeeklyReportValidationError, generate_weekly_report  # noqa: E402
from backend.services.workday_policy import is_workday  # noqa: E402
from evals.scripts.score_utils import case_result, load_cases, write_result  # noqa: E402


def run() -> dict[str, Any]:
    cases = load_cases("workday_policy_cases.json")
    results = [_run_case(case) for case in cases]
    return write_result("workday_policy", results)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    day = date.fromisoformat(case["date"])
    hard: list[str] = []
    evidence: list[str] = []
    score = 100
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        try:
            if expected == "workday_goal_created":
                result = get_or_generate_today_goal(db_path, day)
                if not result.created:
                    hard.append("goal_not_created")
                evidence.append(f"created={result.created}")
            elif expected == "same_goal_reused":
                first = get_or_generate_today_goal(db_path, day)
                second = get_or_generate_today_goal(db_path, day)
                if first.goal["daily_goal"]["id"] != second.goal["daily_goal"]["id"] or second.created:
                    hard.append("same_day_not_reused")
                evidence.append(f"goal_id={first.goal['daily_goal']['id']}")
            elif expected == "tomorrow_direction_used":
                _seed_previous_checkin(db_path, day - timedelta(days=1), "继续同一后端重点")
                result = get_or_generate_today_goal(db_path, day)
                snapshot = result.goal["daily_goal"]["context_snapshot"]
                if snapshot.get("tomorrow_direction") != "继续同一后端重点":
                    hard.append("tomorrow_direction_missing")
                evidence.append(f"tomorrow_direction={snapshot.get('tomorrow_direction')}")
            elif expected == "weekly_report_not_ready":
                _seed_week_for_report(db_path, include_friday=False)
                try:
                    generate_weekly_report(db_path, {"week_id": "2026-W10"}, default_date=day)
                    hard.append("weekly_report_generated_before_friday_checkin")
                except WeeklyReportValidationError:
                    evidence.append("weekly report rejected before Friday check-in")
            elif expected == "friday_checkin_enables_report":
                goal_id = _seed_goal(db_path, day)
                result = save_daily_checkin(
                    db_path,
                    {
                        "date": day.isoformat(),
                        "goal_id": goal_id,
                        "completion_text": "完成周五目标。",
                        "felt_difficulty": 3,
                    },
                    default_date=day,
                )
                if not result.can_generate_weekly_report:
                    hard.append("friday_checkin_not_enabled")
                evidence.append(f"can_generate_weekly_report={result.can_generate_weekly_report}")
            elif expected == "non_workday_skip":
                if is_workday(day):
                    hard.append("date_unexpectedly_workday")
                evidence.append(f"is_workday={is_workday(day)}")
        except Exception as exc:  # noqa: BLE001 - eval should report failure
            hard.append(f"case_error:{exc}")
            score = 0

    if hard:
        score = 0
    return case_result(case["id"], "workday_policy", score, hard, evidence, "修复 Workday Policy 或周报触发规则。")


def _seed_previous_checkin(db_path: Path, day: date, tomorrow_direction: str) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(connection, id=1, long_term_direction="Build DayPilot MVP.")
            goal_id = _seed_goal_in_connection(connection, day)
            repo.create_daily_checkin(
                connection,
                daily_goal_id=goal_id,
                checkin_date=day.isoformat(),
                week_id=repo.week_id_for_date(day),
                completion_text="完成前一天目标。",
                felt_difficulty=3,
                tomorrow_direction=tomorrow_direction,
                parsed_completion_rate=1.0,
                completed_items=["前一天目标"],
                unfinished_items=[],
                blockers=[],
                actual_outputs=["artifact"],
                processor_snapshot={"source": "workday-eval"},
            )
    finally:
        connection.close()


def _seed_goal(db_path: Path, day: date) -> int:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(connection, id=1, long_term_direction="Build DayPilot MVP.")
            return _seed_goal_in_connection(connection, day)
    finally:
        connection.close()


def _seed_goal_in_connection(connection, day: date) -> int:
    daily_goal_id = repo.create_daily_goal(
        connection,
        goal_date=day.isoformat(),
        context_snapshot={"source": "workday-eval"},
        generated_at=f"{day.isoformat()} 09:00:00",
    )
    repo.create_goal_version(
        connection,
        daily_goal_id=daily_goal_id,
        version_no=1,
        is_active=1,
        main_goal="完成工作日策略验证切片",
        goal_reason="Eval seed.",
        success_criteria=["交付验证切片", "记录结果"],
        estimated_minutes=60,
        difficulty_level=3,
        minimum_version="留下验证记录。",
        goal_type="coding",
        revision_source="initial_generation",
    )
    return daily_goal_id


def _seed_week_for_report(db_path: Path, *, include_friday: bool) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(connection, id=1, long_term_direction="Build DayPilot MVP.")
            start = date(2026, 3, 2)
            for offset in range(5):
                day = start + timedelta(days=offset)
                goal_id = _seed_goal_in_connection(connection, day)
                if offset < 4 or include_friday:
                    repo.create_daily_checkin(
                        connection,
                        daily_goal_id=goal_id,
                        checkin_date=day.isoformat(),
                        week_id="2026-W10",
                        completion_text="完成当天目标。",
                        felt_difficulty=3,
                        parsed_completion_rate=1.0,
                        completed_items=["当天目标"],
                        unfinished_items=[],
                        blockers=[],
                        actual_outputs=["artifact"],
                        processor_snapshot={"source": "workday-eval"},
                    )
    finally:
        connection.close()


def main() -> None:
    summary = run()
    print(f"workday_policy: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
