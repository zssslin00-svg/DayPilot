from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.goal_critic import ensure_goal_quality, review_goal  # noqa: E402
from backend.services.goal_generation_resources import validate_daily_goal_output  # noqa: E402


def test_goal_critic_passes_good_goal() -> None:
    result = review_goal(_good_goal(), flow="generation")

    assert result.passed is True
    assert result.score >= 85
    assert result.failed_rules == []


def test_goal_critic_rewrites_vague_goal() -> None:
    bad_goal = _good_goal()
    bad_goal["main_goal"] = "继续学习 Agent 能力"
    bad_goal["minimum_acceptable_result"] = "继续学习 Agent 能力"

    result = ensure_goal_quality(bad_goal, flow="generation")

    assert result.quality_status == "rewritten_passed"
    assert result.review["passed"] is True
    assert "交付明确成果" in result.goal["main_goal"]
    validate_daily_goal_output(result.goal)


def test_goal_critic_rewrites_oversized_goal() -> None:
    bad_goal = _good_goal()
    bad_goal["main_goal"] = "完成整个 DayPilot 后端端到端联调"
    bad_goal["estimated_minutes"] = 240
    bad_goal["completion_criteria"] = [
        "完成后端所有接口",
        "完成前端所有页面",
        "完成周报生成",
        "完成反馈修正",
        "完成全部测试",
    ]

    result = ensure_goal_quality(bad_goal, flow="generation")

    assert result.review["passed"] is True
    assert result.goal["estimated_minutes"] <= 150
    assert len(result.goal["completion_criteria"]) <= 5
    assert "整个" not in result.goal["main_goal"]
    validate_daily_goal_output(result.goal)


def _good_goal() -> dict:
    return {
        "schema_version": "daily_goal.v1",
        "goal_date": "2026-06-08",
        "main_goal": "完成 DayPilot 目标质量审查服务的最小代码改动",
        "rationale": "该目标能在今天形成可运行的审查门禁，直接提升每日目标生成质量。",
        "completion_criteria": [
            "实现目标质量审查纯函数",
            "保存审查结果到 goal_versions",
            "补充一个坏目标重写测试",
        ],
        "estimated_minutes": 80,
        "difficulty": 3,
        "minimum_acceptable_result": "至少留下一段可运行的审查函数和测试记录。",
        "stretch_challenge": "补充一个降级目标测试样例。",
        "do_not_do_today": ["不要实现复杂 LLM 审查调用"],
        "goal_type": "coding",
        "growth_tags": ["daypilot_mvp", "goal_quality"],
        "context_used": {
            "primary_driver": "current_project",
            "tomorrow_direction_handling": "empty_agent_decided",
            "continuity_note": "围绕当前 DayPilot MVP 质量门推进。",
            "difficulty_reason": "目标范围控制在 80 分钟内的单个后端服务。",
        },
    }


def main() -> None:
    test_goal_critic_passes_good_goal()
    test_goal_critic_rewrites_vague_goal()
    test_goal_critic_rewrites_oversized_goal()
    print("PASS: Goal Critic reviews and rewrites daily goals")


if __name__ == "__main__":
    main()
