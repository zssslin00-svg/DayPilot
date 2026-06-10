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
from backend.services.weekly_report_resources import validate_weekly_report_output  # noqa: E402
from backend.services.weekly_report_service import generate_weekly_report  # noqa: E402
from evals.scripts.score_utils import (  # noqa: E402
    case_result,
    has_vague_text,
    has_weekday_log,
    is_outcome_text,
    load_cases,
    write_result,
)


WEEK_START = date(2026, 3, 2)
WEEK_ID = "2026-W10"


def run() -> dict[str, Any]:
    cases = load_cases("weekly_report_cases.json")
    results = [_run_case(case) for case in cases]
    return write_result("weekly_report", results)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        _seed_workweek(db_path, case)
        hard: list[str] = []
        evidence: list[str] = []
        score = 100
        try:
            result = generate_weekly_report(
                db_path,
                {"week_id": WEEK_ID},
                default_date=date(2026, 3, 6),
            )
            report = result.report_output
            validate_weekly_report_output(report)
        except Exception as exc:  # noqa: BLE001 - eval should report generation failure
            return case_result(case["id"], "weekly_report", 0, [f"generation_failed:{exc}"], [], "修复周报聚合或质量规则。")

    if set(report) != {"completed_work", "next_week_plan", "weekly_reflection"}:
        hard.append("missing_sections")
        score -= 30
    all_text = " ".join(item for section in report.values() for item in section)
    if has_weekday_log(all_text):
        hard.append("weekday_log")
        score -= 25
    if any(has_vague_text(item) for section in report.values() for item in section):
        hard.append("vague_text")
        score -= 15
    if not all(is_outcome_text(item) for item in report["next_week_plan"]):
        hard.append("next_week_not_outcome")
        score -= 20
    if len(result.weekly_focus) < 2:
        hard.append("weekly_focus_missing")
        score -= 15
    if "scope_review" in case["expected"]["must"] and not any(
        token in " ".join(report["weekly_reflection"]) for token in ("范围", "最低版本", "收敛")
    ):
        hard.append("reflection_missing_scope")
        score -= 15
    unfinished = case["input"].get("unfinished") or []
    if any(item in " ".join(report["completed_work"]) for item in unfinished):
        hard.append("unfinished_as_done")
        score -= 25

    evidence.append("sections=" + ",".join(report.keys()))
    evidence.append(f"weekly_focus_count={len(result.weekly_focus)}")
    evidence.append("next_week_plan=" + " / ".join(report["next_week_plan"]))
    return case_result(case["id"], "weekly_report", score, hard, evidence, "修正 Weekly Report Generator 或质量审查规则。")


def _seed_workweek(db_path: Path, case: dict[str, Any]) -> None:
    connection = initialize_database(db_path)
    theme = case["input"].get("theme") or "weekly report generator"
    friday_direction = case["input"].get("friday_direction", "下周补 eval 和 weekly_focus 承接")
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a reliable daily-goal and weekly-review loop.",
                current_focus_projects=["DayPilot MVP", theme],
                default_available_minutes=90,
            )
            for offset in range(5):
                day = WEEK_START + timedelta(days=offset)
                item = f"{theme} eval slice {offset + 1}"
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date=day.isoformat(),
                    context_snapshot={"source": "weekly-report-eval"},
                    generated_at=f"{day.isoformat()} 09:00:00",
                )
                version_id = repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal=f"完成 DayPilot {item} 的可验收产出",
                    goal_reason=f"{item} supports weekly report eval.",
                    success_criteria=[f"交付 {item}", "记录验收结果"],
                    estimated_minutes=70,
                    difficulty_level=4 if case["input"].get("hard_week") else 3,
                    minimum_version=f"{item} 有可检查记录。",
                    stretch_challenge="补充一条回归测试。",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                if case["input"].get("high_revision") and offset in {1, 2}:
                    feedback_id = repo.create_feedback_message(
                        connection,
                        daily_goal_id=daily_goal_id,
                        before_version_id=version_id,
                        raw_message="目标太大，先做最低版本。",
                        feedback_type="quality_issue",
                        affected_scope="today",
                        interpretation_json={"summary": "缩小范围。"},
                        extracted_constraints={},
                        extracted_preferences={},
                        memory_action="none",
                        should_regenerate_goal=1,
                        is_resolved=1,
                    )
                    version_id = repo.create_goal_version(
                        connection,
                        daily_goal_id=daily_goal_id,
                        version_no=2,
                        is_active=1,
                        main_goal=f"缩小范围：完成 DayPilot {item} 的最低版本产出",
                        goal_reason="根据反馈缩小范围。",
                        success_criteria=[f"交付 {item} 最低版本", "记录验收结果"],
                        estimated_minutes=60,
                        difficulty_level=3,
                        minimum_version=f"{item} 最低版本有记录。",
                        goal_type="coding",
                        revision_source="user_feedback",
                        feedback_message_id=feedback_id,
                    )
                    repo.update_feedback_message(connection, feedback_id, after_version_id=version_id)

                rate = 0.9 if offset in {0, 1, 4} else 0.65
                unfinished = case["input"].get("unfinished") if offset == 3 else []
                blockers = ["外部依赖未确认"] if case["input"].get("blocker") and offset == 3 else []
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date=day.isoformat(),
                    week_id=WEEK_ID,
                    completion_text=f"完成 {item}，留下可复查记录。",
                    felt_difficulty=5 if case["input"].get("hard_week") else 3,
                    tomorrow_direction=friday_direction if offset == 4 else f"继续 {theme} 下一切片",
                    parsed_completion_rate=rate,
                    completed_items=[item],
                    unfinished_items=unfinished or [],
                    blockers=blockers,
                    actual_outputs=[f"artifact/{case['id']}/{offset + 1}"],
                    processor_snapshot={"source": "weekly-report-eval"},
                )
            repo.create_ability_state(
                connection,
                state_date="2026-03-06",
                current_difficulty=3.0,
                target_difficulty_level=3,
                recent_completion_rate=0.75,
                recent_felt_difficulty_avg=4.0 if case["input"].get("hard_week") else 3.0,
                default_estimated_minutes=90,
                preferred_goal_type_weights={"coding": 0.6, "testing": 0.4},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["流水账", "空话"],
                adjustment_direction="hold",
                update_reason="Eval seed.",
                is_current=1,
            )
    finally:
        connection.close()


def main() -> None:
    summary = run()
    print(f"weekly_report: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
