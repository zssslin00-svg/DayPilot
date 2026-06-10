from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.schemas.json_schema import validate_json_schema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DAILY_GOAL_SCHEMA_PATH = PROJECT_ROOT / "backend" / "schemas" / "daily_goal.schema.json"
GOAL_GENERATION_SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "goal_generation" / "system_prompt.md"
GOAL_GENERATION_USER_PROMPT_TEMPLATE_PATH = (
    PROJECT_ROOT / "prompts" / "goal_generation" / "user_prompt_template.md"
)
DAILY_GOAL_EXAMPLES_PATH = PROJECT_ROOT / "prompts" / "examples" / "daily_goal_examples.json"
DAILY_GOAL_TOP_LEVEL_KEYS = (
    "schema_version",
    "goal_date",
    "main_goal",
    "rationale",
    "completion_criteria",
    "estimated_minutes",
    "difficulty",
    "minimum_acceptable_result",
    "stretch_challenge",
    "do_not_do_today",
    "goal_type",
    "growth_tags",
    "context_used",
)
DAILY_GOAL_TYPES = {
    "design",
    "coding",
    "documentation",
    "review",
    "learning",
    "research",
    "debugging",
    "testing",
    "planning",
}
DAILY_GOAL_PRIMARY_DRIVERS = {
    "tomorrow_direction",
    "last_week_focus",
    "current_project",
    "recent_unfinished_work",
    "difficulty_adjustment",
    "agent_decision",
}
DAILY_GOAL_TOMORROW_HANDLING = {
    "empty_agent_decided",
    "used_as_given",
    "narrowed_to_daily_scope",
    "partially_used",
    "ignored_due_to_mismatch",
}
DAILY_GOAL_CONTEXT_KEYS = (
    "primary_driver",
    "tomorrow_direction_handling",
    "continuity_note",
    "difficulty_reason",
)
DEFAULT_DAILY_GOAL_TAGS = ["daypilot_mvp", "daily_goal", "agent_workflow"]
DAILY_GOAL_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")


@dataclass(frozen=True)
class DailyGoalGenerationResources:
    schema: dict[str, Any]
    system_prompt: str
    user_prompt_template: str


def load_daily_goal_generation_resources() -> DailyGoalGenerationResources:
    """Load the reusable prompt and schema resources for daily goal generation."""

    return DailyGoalGenerationResources(
        schema=load_daily_goal_schema(),
        system_prompt=GOAL_GENERATION_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
        user_prompt_template=GOAL_GENERATION_USER_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8"),
    )


def load_daily_goal_schema() -> dict[str, Any]:
    """Load the DailyGoalOutput JSON Schema."""

    return json.loads(DAILY_GOAL_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_daily_goal_examples() -> list[dict[str, Any]]:
    """Load example daily goals used by prompt examples and schema tests."""

    return json.loads(DAILY_GOAL_EXAMPLES_PATH.read_text(encoding="utf-8"))


def validate_daily_goal_output(goal: dict[str, Any]) -> None:
    """Validate one DailyGoalOutput object against the project schema."""

    validate_json_schema(goal, load_daily_goal_schema())


def daily_goal_output_contract() -> dict[str, Any]:
    """Return a compact machine-readable output contract for LLM prompts."""

    return {
        "schema_version": "daily_goal.v1",
        "additional_properties": False,
        "required_fields": list(DAILY_GOAL_TOP_LEVEL_KEYS),
        "array_fields": {
            "completion_criteria": "2-5 strings",
            "do_not_do_today": "1-4 strings; never a plain string",
            "growth_tags": "1-5 lowercase English slugs matching ^[a-z0-9][a-z0-9_-]{1,39}$",
        },
        "integer_ranges": {
            "estimated_minutes": [30, 150],
            "difficulty": [1, 5],
        },
        "goal_type_values": sorted(DAILY_GOAL_TYPES),
        "context_used": {
            "additional_properties": False,
            "required_fields": list(DAILY_GOAL_CONTEXT_KEYS),
            "primary_driver_values": sorted(DAILY_GOAL_PRIMARY_DRIVERS),
            "tomorrow_direction_handling_values": sorted(DAILY_GOAL_TOMORROW_HANDLING),
        },
        "hard_rules": [
            "Use Chinese only for user-facing prose fields.",
            "Use English lowercase slug strings for growth_tags.",
            "Return context_used.primary_driver, not project_priority or weekly_focus_alignment.",
        ],
    }


def daily_goal_repair_hint() -> dict[str, Any]:
    """Return the daily goal schema hints used by repair prompts."""

    return {
        "task_schema": "daily_goal.v1",
        "output_contract": daily_goal_output_contract(),
        "safe_repairs": [
            "Convert do_not_do_today from a JSON string or plain string into a string array.",
            "Replace invalid growth_tags with lowercase English slugs.",
            "Remove unknown context_used keys and fill required context_used keys.",
            "Clamp estimated_minutes and difficulty into their schema ranges.",
            "Do not rewrite main_goal, rationale, completion_criteria, or other business meaning.",
        ],
    }


def compact_daily_goal_example() -> dict[str, Any]:
    """Return a small valid example that demonstrates strict schema fields."""

    return {
        "schema_version": "daily_goal.v1",
        "goal_date": "2026-06-09",
        "main_goal": "完成 DayPilot 目标生成解析链路的一处可验证修复",
        "rationale": "今天优先修复真实使用中暴露的解析失败，保证目标生成可以稳定落库。",
        "completion_criteria": [
            "补齐输出字段约束并通过 schema 校验",
            "记录一条可复现的解析成功验证结果",
        ],
        "estimated_minutes": 60,
        "difficulty": 2,
        "minimum_acceptable_result": "至少修复一种会触发 fallback 的字段格式问题。",
        "stretch_challenge": "补充一个 repair 成功的回归测试。",
        "do_not_do_today": ["不要扩展到周报或外部系统集成"],
        "goal_type": "coding",
        "growth_tags": ["daypilot_mvp", "daily_goal", "structured_output"],
        "context_used": {
            "primary_driver": "current_project",
            "tomorrow_direction_handling": "empty_agent_decided",
            "continuity_note": "基于当前项目的真实解析问题，选择今天可完成的修复切片。",
            "difficulty_reason": "任务限制在字段格式和校验链路内，预计一小时内可完成。",
        },
    }


def normalize_daily_goal_output(
    output: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safely normalize low-risk daily goal schema shape issues."""

    context = context or {}
    normalized = {
        key: value for key, value in dict(output).items() if key in DAILY_GOAL_TOP_LEVEL_KEYS
    }
    normalized["growth_tags"] = _normalize_growth_tags(normalized.get("growth_tags"))
    normalized["context_used"] = _normalize_context_used(
        normalized.get("context_used"),
        context,
    )
    normalized["do_not_do_today"] = _coerce_string_list(
        normalized.get("do_not_do_today"),
        fallback=["不要扩展到今日目标范围外"],
        min_length=6,
        max_items=4,
    )
    normalized["estimated_minutes"] = _clamp_int(
        normalized.get("estimated_minutes"),
        30,
        150,
        _default_estimated_minutes(context),
    )
    normalized["difficulty"] = _clamp_int(
        normalized.get("difficulty"),
        1,
        5,
        _default_difficulty(context),
    )
    if normalized.get("goal_type") not in DAILY_GOAL_TYPES:
        normalized["goal_type"] = _default_goal_type(context)
    return normalized


def _normalize_growth_tags(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else re.split(r"[,\s]+", str(value or ""))
    tags: list[str] = []
    for item in raw_items:
        tag = str(item or "").strip().lower()
        if DAILY_GOAL_TAG_RE.match(tag) and tag not in tags:
            tags.append(tag)
    for tag in DEFAULT_DAILY_GOAL_TAGS:
        if tag not in tags:
            tags.append(tag)
    return tags[:5]


def _normalize_context_used(value: Any, context: dict[str, Any]) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    primary_driver = str(raw.get("primary_driver") or "")
    if primary_driver not in DAILY_GOAL_PRIMARY_DRIVERS:
        primary_driver = _infer_primary_driver(context)

    tomorrow_handling = str(raw.get("tomorrow_direction_handling") or "")
    if tomorrow_handling not in DAILY_GOAL_TOMORROW_HANDLING:
        tomorrow_handling = _infer_tomorrow_handling(context, primary_driver)

    return {
        "primary_driver": primary_driver,
        "tomorrow_direction_handling": tomorrow_handling,
        "continuity_note": _bounded_text(
            raw.get("continuity_note"),
            _default_continuity_note(context, primary_driver),
            max_length=260,
        ),
        "difficulty_reason": _bounded_text(
            raw.get("difficulty_reason"),
            "Difficulty and time budget were normalized from ability state and schema limits.",
            max_length=260,
        ),
    }


def _infer_primary_driver(context: dict[str, Any]) -> str:
    if context.get("selected_weekly_focus"):
        return "last_week_focus"
    if str(context.get("tomorrow_direction") or "").strip():
        return "tomorrow_direction"
    if context.get("project"):
        return "current_project"
    return "agent_decision"


def _infer_tomorrow_handling(context: dict[str, Any], primary_driver: str) -> str:
    has_tomorrow = bool(str(context.get("tomorrow_direction") or "").strip())
    if not has_tomorrow:
        return "empty_agent_decided"
    if primary_driver == "tomorrow_direction":
        return "used_as_given"
    return "partially_used"


def _default_continuity_note(context: dict[str, Any], primary_driver: str) -> str:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    project_name = str(project.get("name") or "current project").strip()
    if primary_driver == "last_week_focus":
        return "Selected a daily-sized slice from the active weekly focus."
    if primary_driver == "tomorrow_direction":
        return "Used the latest tomorrow direction and narrowed it to today's deliverable."
    if primary_driver == "current_project":
        return f"Generated from the active project context: {project_name}."
    return "Generated from available DayPilot planning context."


def _default_estimated_minutes(context: dict[str, Any]) -> int:
    ability_state = context.get("ability_state") if isinstance(context.get("ability_state"), dict) else {}
    profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
    return _clamp_int(
        ability_state.get("default_estimated_minutes") or profile.get("default_available_minutes"),
        30,
        150,
        60,
    )


def _default_difficulty(context: dict[str, Any]) -> int:
    ability_state = context.get("ability_state") if isinstance(context.get("ability_state"), dict) else {}
    return _clamp_int(
        ability_state.get("target_difficulty_level") or ability_state.get("current_difficulty"),
        1,
        5,
        2,
    )


def _default_goal_type(context: dict[str, Any]) -> str:
    selected_focus = (
        context.get("selected_weekly_focus")
        if isinstance(context.get("selected_weekly_focus"), dict)
        else {}
    )
    focus_type = str(selected_focus.get("focus_type") or "")
    return focus_type if focus_type in DAILY_GOAL_TYPES else "coding"


def _coerce_string_list(
    value: Any,
    *,
    fallback: list[str],
    min_length: int,
    max_items: int,
) -> list[str]:
    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = stripped
        else:
            parsed = stripped

    if isinstance(parsed, list):
        candidates = parsed
    elif parsed:
        candidates = [parsed]
    else:
        candidates = []

    items: list[str] = []
    for candidate in candidates:
        text = " ".join(str(candidate or "").split())
        if len(text) >= min_length and text not in items:
            items.append(text)
    if not items:
        items = list(fallback)
    return items[:max_items]


def _bounded_text(value: Any, fallback: str, *, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) < 8:
        text = fallback
    return text[:max_length]


def _clamp_int(value: Any, lower: int, upper: int, default: int) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
