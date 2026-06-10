from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.goal_generation_resources import (  # noqa: E402
    load_daily_goal_examples,
    load_daily_goal_generation_resources,
    normalize_daily_goal_output,
    validate_daily_goal_output,
)


def test_daily_goal_generation_resources_load() -> None:
    resources = load_daily_goal_generation_resources()

    assert resources.schema["title"] == "DayPilotDailyGoalOutput"
    assert "Goal Generator" in resources.system_prompt
    assert "{{goal_date}}" in resources.user_prompt_template


def test_daily_goal_examples_match_schema() -> None:
    examples = load_daily_goal_examples()

    assert len(examples) == 3
    for example in examples:
        validate_daily_goal_output(example)


def test_daily_goal_normalization_repairs_common_llm_shape_issues() -> None:
    raw_goal = {
        "schema_version": "daily_goal.v1",
        "goal_date": "2026-06-09",
        "main_goal": "Fix DayPilot parser fallback path",
        "rationale": "The current goal generation output has minor schema shape issues that should not force mock fallback.",
        "completion_criteria": [
            "Normalize growth tag values before validation",
            "Fill required context_used fields safely",
        ],
        "estimated_minutes": "220",
        "difficulty": "3.8",
        "minimum_acceptable_result": "One repairable schema-shape issue validates without mock fallback.",
        "stretch_challenge": "Add one regression test for the repaired output.",
        "do_not_do_today": "[\"不要扩展到周报生成\",\"不要新增外部系统集成\"]",
        "goal_type": "implementation",
        "growth_tags": ["数据构建方案", "规则标注"],
        "context_used": {
            "project_priority": "P2",
            "weekly_focus_alignment": "not_applicable",
            "difficulty_reason": "Use the ability state budget and clamp schema ranges.",
        },
        "extra_field": "must be removed",
    }

    normalized = normalize_daily_goal_output(
        raw_goal,
        {
            "selected_weekly_focus": {"focus_type": "coding"},
            "ability_state": {
                "default_estimated_minutes": 90,
                "target_difficulty_level": 3,
            },
        },
    )

    validate_daily_goal_output(normalized)
    assert normalized["estimated_minutes"] == 150
    assert normalized["difficulty"] == 3
    assert normalized["goal_type"] == "coding"
    assert normalized["growth_tags"] == ["daypilot_mvp", "daily_goal", "agent_workflow"]
    assert normalized["do_not_do_today"] == ["不要扩展到周报生成", "不要新增外部系统集成"]
    assert normalized["context_used"]["primary_driver"] == "last_week_focus"
    assert set(normalized["context_used"]) == {
        "primary_driver",
        "tomorrow_direction_handling",
        "continuity_note",
        "difficulty_reason",
    }
    assert "extra_field" not in normalized


def main() -> None:
    test_daily_goal_generation_resources_load()
    test_daily_goal_examples_match_schema()
    test_daily_goal_normalization_repairs_common_llm_shape_issues()
    print("PASS: daily goal schema, prompts, and examples load and validate")


if __name__ == "__main__":
    main()
