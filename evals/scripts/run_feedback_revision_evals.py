from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import connect_database, initialize_database  # noqa: E402
from backend.services.goal_feedback_service import revise_goal_from_feedback  # noqa: E402
from evals.scripts.score_utils import (  # noqa: E402
    case_result,
    has_deliverable,
    has_multi_goal_marker,
    load_cases,
    write_result,
)


EVAL_DATE = date(2026, 3, 2)


def run() -> dict[str, Any]:
    cases = load_cases("feedback_revision_cases.json")
    results = [_run_case(case) for case in cases]
    return write_result("feedback_revision", results)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        goal_id, initial_version_id = _seed_goal(db_path)
        messages = case["input"].get("messages") or [case["input"]["message"]]
        latest_payload = None
        for message in messages:
            latest_payload = revise_goal_from_feedback(
                db_path,
                {"date": EVAL_DATE.isoformat(), "goal_id": goal_id, "message": message},
                default_date=EVAL_DATE,
            )
        connection = connect_database(db_path)
        try:
            versions = repo.list_goal_versions(connection, goal_id)
            active = [item for item in versions if item["is_active"] == 1][0]
            feedback_count = connection.execute("SELECT COUNT(*) FROM feedback_messages").fetchone()[0]
        finally:
            connection.close()

    assert latest_payload is not None
    output = latest_payload.updated_goal["goal_output"]
    signal = latest_payload.feedback_signal
    expected = case["expected"]["must"]
    hard: list[str] = []
    evidence = [f"main_goal={output['main_goal']}", f"feedback_types={signal['feedback_types']}"]
    score = 100

    if len(versions) <= 1 or active["id"] == initial_version_id or feedback_count != len(messages):
        hard.append("version_saved_failed")
        score -= 30
    if has_multi_goal_marker(output["main_goal"]):
        hard.append("multi_goal")
        score -= 20
    if "scope_shrink" in expected and not (
        output["estimated_minutes"] <= 90 and len(output["completion_criteria"]) <= 2
    ):
        hard.append("scope_not_shrunk")
        score -= 20
    if "minutes_lte_45" in expected and output["estimated_minutes"] > 45:
        hard.append("time_limit_ignored")
        score -= 25
    if "criteria_reduced" in expected and len(output["completion_criteria"]) > 2:
        hard.append("criteria_not_reduced")
        score -= 10
    if "goal_type_coding" in expected and output["goal_type"] != "coding":
        hard.append("goal_type_not_coding")
        score -= 20
    if "clear_criteria" in expected and not all(len(item) >= 8 for item in output["completion_criteria"]):
        hard.append("criteria_not_clear")
        score -= 15
    if "deliverable_required" in expected and not has_deliverable(
        " ".join([output["main_goal"], output["minimum_acceptable_result"]])
    ):
        hard.append("deliverable_missing")
        score -= 15
    if "short_term_memory" in expected and latest_payload.memory_update["scope"] != "short_term":
        hard.append("short_term_memory_missing")
        score -= 15
    if "long_term_memory" in expected and latest_payload.memory_update["scope"] != "long_term":
        hard.append("long_term_memory_missing")
        score -= 15
    if "three_versions" in expected and len(versions) < 4:
        hard.append("version_chain_too_short")
        score -= 20
    if "preserve_goal_type" in expected and output["goal_type"] != "documentation":
        hard.append("goal_type_not_preserved")
        score -= 10

    evidence.append(f"estimated_minutes={output['estimated_minutes']}")
    evidence.append(f"version_count={len(versions)}")
    return case_result(case["id"], "feedback_revision", score, hard, evidence, "修正 Feedback Interpreter 或 Goal Revision 规则。")


def _seed_goal(db_path: Path) -> tuple[int, int]:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful DayPilot MVP.",
                current_focus_projects=["DayPilot MVP"],
                default_available_minutes=120,
            )
            daily_goal_id = repo.create_daily_goal(
                connection,
                goal_date=EVAL_DATE.isoformat(),
                context_snapshot={
                    "goal_output_context_used": {
                        "primary_driver": "current_project",
                        "tomorrow_direction_handling": "empty_agent_decided",
                        "continuity_note": "Eval seed.",
                        "difficulty_reason": "Eval seed.",
                    }
                },
                generated_at=f"{EVAL_DATE.isoformat()} 09:00:00",
            )
            version_id = repo.create_goal_version(
                connection,
                daily_goal_id=daily_goal_id,
                version_no=1,
                is_active=1,
                main_goal="完成完整周报模块的文档、接口、前端展示和评估",
                goal_reason="Eval seed initial goal.",
                success_criteria=[
                    "写完周报设计文档",
                    "实现周报生成接口",
                    "完成前端展示",
                    "补齐评估用例",
                ],
                estimated_minutes=120,
                difficulty_level=4,
                minimum_version="周报模块有一份完整可检查交付。",
                stretch_challenge="补充复制和重新生成入口。",
                avoid_today=json.dumps(["不要接外部文档系统"], ensure_ascii=False),
                goal_type="documentation",
                revision_source="initial_generation",
            )
            return daily_goal_id, version_id
    finally:
        connection.close()


def main() -> None:
    summary = run()
    print(f"feedback_revision: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
