from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.career_chat_service import send_career_chat_message  # noqa: E402
from evals.scripts.score_utils import case_result, load_cases, write_result  # noqa: E402


EVAL_DATE = date(2026, 6, 11)
DELIVERABLE_WORDS = ["项目", "实验", "文档", "代码", "记录", "作品", "demo", "交付", "表格", "页面"]
PURE_LEARNING_WORDS = ["纯学习", "看看", "读一读", "了解一下"]


def run() -> dict[str, Any]:
    cases = load_cases("career_chat_cases.json")
    results = [_run_case(case) for case in cases]
    return write_result("career_chat", results)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / f"{case['id']}.sqlite3"
        soul_path = Path(temp_dir) / "SOUL.md"
        soul_path.write_text("# DayPilot SOUL\n\n## 长期方向\n\n项目驱动成长。\n", encoding="utf-8")
        _seed_profile(db_path, case)
        result = send_career_chat_message(
            db_path,
            {"message": case["input"]["message"], "available_minutes": 60},
            soul_path=soul_path,
            today=EVAL_DATE,
        ).payload

    recommendations = result["recommendations"]
    suggestions = result["profile_update_suggestions"]
    assistant_message = result["assistant_message"]
    assistant_text = assistant_message.get("content", "") if isinstance(assistant_message, dict) else str(assistant_message)
    joined = assistant_text + " " + " ".join(
        " ".join(
            [
                item.get("title", ""),
                item.get("why_it_fits", ""),
                item.get("deliverable", ""),
                item.get("first_step", ""),
            ]
        )
        for item in recommendations
    )
    hard: list[str] = []
    evidence = [f"recommendations={len(recommendations)}", f"suggestions={len(suggestions)}"]
    score = 100
    expected = case["expected"]["must"]

    if len(recommendations) > 6:
        hard.append("too_many_recommendations")
        score -= 30
    if "deliverable" in expected and recommendations and not all(_has_deliverable(item) for item in recommendations):
        hard.append("deliverable_missing")
        score -= 25
    if "agent_aligned" in expected and "Agent" not in joined and "智能体" not in joined:
        hard.append("agent_alignment_missing")
        score -= 20
    if "profile_suggestion" in expected and not suggestions:
        hard.append("profile_suggestion_missing")
        score -= 20
    if "conservative_profile_first" in expected and "画像" not in joined and not suggestions:
        hard.append("profile_first_step_missing")
        score -= 15
    if "not_pure_learning" in expected and any(_is_pure_learning(item) for item in recommendations):
        hard.append("pure_learning_recommendation")
        score -= 20
    if any("市场" in item.get("why_it_fits", "") or "薪资" in item.get("why_it_fits", "") for item in recommendations):
        hard.append("external_market_claim")
        score -= 25

    evidence.append(f"first_title={recommendations[0]['title'] if recommendations else '-'}")
    return case_result(case["id"], "career_chat", score, hard, evidence, "收紧职业规划建议的画像贴合和交付物约束。")


def _seed_profile(db_path: Path, case: dict[str, Any]) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a flexible career system through project-based learning.",
                career_profile=case["input"].get("career_profile") or {},
                default_available_minutes=90,
            )
    finally:
        connection.close()


def _has_deliverable(recommendation: dict[str, Any]) -> bool:
    text = " ".join(
        str(recommendation.get(key, ""))
        for key in ["title", "deliverable", "first_step", "why_it_fits"]
    ).lower()
    return any(word.lower() in text for word in DELIVERABLE_WORDS)


def _is_pure_learning(recommendation: dict[str, Any]) -> bool:
    text = " ".join(
        str(recommendation.get(key, ""))
        for key in ["title", "deliverable", "first_step"]
    )
    return any(word in text for word in PURE_LEARNING_WORDS) and not _has_deliverable(recommendation)


def main() -> None:
    summary = run()
    print(f"career_chat: pass {summary['passed']}/{summary['total']}, average {summary['average_score']}")


if __name__ == "__main__":
    main()
