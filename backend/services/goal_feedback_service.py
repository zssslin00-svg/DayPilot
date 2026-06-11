from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.services.goal_critic import ensure_goal_quality
from backend.services.goal_generation_resources import validate_daily_goal_output
from backend.services.llm_client import generate_json_with_fallback
from backend.services.profile_memory_service import apply_profile_memory_from_feedback
from backend.services.soul_context import SOUL_PATH
from backend.services.today_goal_service import (
    goal_output_from_record,
    sync_current_projects_to_soul_if_requested,
    update_project_today_goal_from_output,
)


PROMPT_VERSION_MOCK = "goal_revision_v1_mock"
PROMPT_VERSION_DEEPSEEK = "goal_revision_v2_deepseek"
MOCK_MODEL_NAME = "mock-goal-revision-adapter"


@dataclass(frozen=True)
class GoalFeedbackResult:
    updated_goal: dict[str, Any]
    feedback_message: dict[str, Any]
    feedback_signal: dict[str, Any]
    memory_update: dict[str, Any]


class GoalFeedbackValidationError(ValueError):
    """Raised when a goal feedback request is outside the MVP contract."""


class GoalFeedbackPersistenceError(RuntimeError):
    """Raised when valid feedback cannot be persisted."""


def revise_goal_from_feedback(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    default_date: date,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
) -> GoalFeedbackResult:
    feedback_date = _parse_feedback_date(request_body.get("date"), default_date)
    message = str(request_body.get("message") or "").strip()
    if not message:
        raise GoalFeedbackValidationError("message 不能为空。")

    connection = initialize_database(db_path)
    sync_source_goal_id: int | None = None
    try:
        with connection:
            goal_record = _resolve_goal_record(connection, feedback_date, request_body.get("goal_id"))
            daily_goal = goal_record["daily_goal"]
            active_version = goal_record["active_version"]
            current_goal = goal_output_from_record(goal_record)
            if current_goal is None:
                raise GoalFeedbackValidationError("当前目标缺少 active version。")

            feedback_signal = interpret_feedback(
                message,
                feedback_date=feedback_date,
                daily_goal_id=int(daily_goal["id"]),
                active_version_id=int(active_version["id"]),
            )
            feedback_id = repo.create_feedback_message(
                connection,
                daily_goal_id=int(daily_goal["id"]),
                before_version_id=int(active_version["id"]),
                raw_message=message,
                feedback_type=_db_feedback_type(feedback_signal),
                affected_scope=_db_affected_scope(feedback_signal),
                interpretation_json=feedback_signal,
                extracted_constraints=feedback_signal["constraints_delta"],
                extracted_preferences=feedback_signal["preference_delta"],
                memory_action=_memory_action(feedback_signal),
                should_regenerate_goal=1,
                is_resolved=0,
            )

            llm_result = generate_json_with_fallback(
                task_name="goal_feedback_revision",
                prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
                prompt_version_mock=PROMPT_VERSION_MOCK,
                mock_model_name=MOCK_MODEL_NAME,
                build_messages=lambda soul: _goal_revision_messages(current_goal, feedback_signal, soul),
                mock_generate=lambda: MockGoalRevisionAgent().revise(current_goal, feedback_signal),
                validator=validate_daily_goal_output,
                settings=settings,
                soul_path=soul_path,
            )
            revised_goal = llm_result.output
            quality_result = ensure_goal_quality(revised_goal, flow="revision")
            revised_goal = quality_result.goal
            validate_daily_goal_output(revised_goal)

            version_no = len(repo.list_goal_versions(connection, int(daily_goal["id"]))) + 1
            version_id = repo.create_goal_version(
                connection,
                daily_goal_id=int(daily_goal["id"]),
                version_no=version_no,
                is_active=1,
                main_goal=revised_goal["main_goal"],
                goal_reason=revised_goal["rationale"],
                success_criteria=revised_goal["completion_criteria"],
                estimated_minutes=revised_goal["estimated_minutes"],
                difficulty_level=revised_goal["difficulty"],
                minimum_version=revised_goal["minimum_acceptable_result"],
                stretch_challenge=revised_goal["stretch_challenge"],
                avoid_today=json.dumps(
                    revised_goal["do_not_do_today"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                goal_type=revised_goal["goal_type"],
                revision_source="user_feedback",
                revision_reason=feedback_signal["interpretation"]["summary"],
                feedback_message_id=feedback_id,
                critic_result={
                    "schema": "daily_goal.v1",
                    "quality_status": quality_result.quality_status,
                    "review": quality_result.review,
                    "llm_metadata": llm_result.metadata,
                },
                prompt_version=llm_result.metadata["prompt_version"],
            )
            snapshot = dict(daily_goal.get("context_snapshot") or {})
            snapshot["last_revision_llm_metadata"] = llm_result.metadata
            repo.update_daily_goal(
                connection,
                int(daily_goal["id"]),
                revision_count=int(daily_goal.get("revision_count") or 0) + 1,
                context_snapshot=snapshot,
            )
            if update_project_today_goal_from_output(
                connection,
                int(daily_goal["project_id"]),
                revised_goal,
                source="goal_feedback_revision",
                daily_goal_id=int(daily_goal["id"]),
            ):
                sync_source_goal_id = int(daily_goal["id"])
            repo.update_feedback_message(
                connection,
                feedback_id,
                after_version_id=version_id,
                is_resolved=1,
            )

            updated_goal = repo.get_goal_with_active_version_by_date_and_project(
                connection,
                feedback_date.isoformat(),
                int(daily_goal["project_id"]),
            )
            feedback_message = repo.get_feedback_message(connection, feedback_id)
            if updated_goal is None or feedback_message is None:
                raise GoalFeedbackPersistenceError("反馈修正保存后无法读取。")

        updated_goal["goal_output"] = revised_goal
        if sync_source_goal_id is not None:
            sync_current_projects_to_soul_if_requested(db_path, soul_path, sync_source_goal_id)
        profile_memory_update = apply_profile_memory_from_feedback(
            db_path,
            int(feedback_message["id"]),
            feedback_signal,
            settings=settings,
            soul_path=soul_path,
        ).payload
        memory_update = {
            "action": _memory_action(feedback_signal),
            "scope": feedback_signal["memory_scope"],
            "suggestions": feedback_signal.get("memory_update_suggestion", []),
            **profile_memory_update,
        }
        return GoalFeedbackResult(
            updated_goal=updated_goal,
            feedback_message=feedback_message,
            feedback_signal=feedback_signal,
            memory_update=memory_update,
        )
    finally:
        connection.close()


def interpret_feedback(
    message: str,
    *,
    feedback_date: date,
    daily_goal_id: int,
    active_version_id: int,
) -> dict[str, Any]:
    normalized = " ".join(message.split())
    feedback_types: list[str] = []
    actions: list[str] = []
    constraints_delta: dict[str, Any] = {
        "available_minutes": _extract_minutes(normalized),
        "must_include": [],
        "must_avoid": [],
        "blocked_goal_types": [],
    }
    preference_delta: dict[str, Any] = {
        "preferred_goal_types": [],
        "deprioritized_goal_types": [],
        "direction_keywords": [],
        "avoid_patterns": [],
        "weight_delta": 0,
    }
    quality_delta: dict[str, Any] = {
        "require_deliverable": False,
        "max_completion_criteria": None,
        "minimum_outcome_must_be_smaller": False,
        "specificity_level": "unchanged",
    }
    difficulty_delta: dict[str, Any] = {
        "difficulty_adjustment": "keep",
        "estimated_minutes_max": None,
        "scope_reduction_ratio": None,
        "criteria_count_max": None,
    }
    direction_delta = {"direction_action": "keep", "from_direction": None, "to_direction": None, "reason": None}

    if constraints_delta["available_minutes"] is not None:
        feedback_types.append("time_limit")
        actions.extend(["shorten_time", "shrink_scope", "reduce_completion_criteria"])
        difficulty_delta["estimated_minutes_max"] = constraints_delta["available_minutes"]
        difficulty_delta["criteria_count_max"] = 2
        quality_delta["max_completion_criteria"] = 2

    if _has_any(normalized, ["太大", "做不完", "范围大", "缩小", "目标太大"]):
        feedback_types.append("scope_too_large")
        actions.extend(["shrink_scope", "reduce_completion_criteria", "adjust_minimum_outcome"])
        difficulty_delta["difficulty_adjustment"] = "decrease"
        difficulty_delta["scope_reduction_ratio"] = 0.65
        difficulty_delta["criteria_count_max"] = 2
        quality_delta["minimum_outcome_must_be_smaller"] = True

    if _has_any(normalized, ["太虚", "不具体", "交付物", "标准不清", "验收不清"]):
        feedback_types.append("goal_too_vague")
        actions.extend(["make_more_specific", "require_deliverable"])
        quality_delta["require_deliverable"] = True
        quality_delta["specificity_level"] = "much_more_specific"

    if _has_any(normalized, ["写代码", "代码", "实现", "coding"]):
        feedback_types.append("prefers_goal_type")
        actions.append("convert_goal_type")
        preference_delta["preferred_goal_types"].append("coding")
        preference_delta["direction_keywords"].append("coding")
        preference_delta["weight_delta"] = 0.1
        direction_delta = {
            "direction_action": "switch",
            "from_direction": None,
            "to_direction": "coding",
            "reason": "用户反馈更想写代码或做实现型任务。",
        }

    if not feedback_types:
        feedback_types.append("other")
        actions.append("keep_goal_with_minor_edit")

    primary = feedback_types[0]
    memory_scope = _memory_scope(normalized, primary)
    return {
        "signal_id": f"feedback-{daily_goal_id}-{active_version_id}-{int(datetime.now().timestamp())}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "date": feedback_date.isoformat(),
        "goal_id": str(daily_goal_id),
        "goal_version_id": str(active_version_id),
        "raw_feedback": message,
        "normalized_feedback": normalized,
        "language": "zh-CN",
        "feedback_types": list(dict.fromkeys(feedback_types)),
        "primary_feedback_type": primary,
        "memory_scope": memory_scope,
        "memory_scope_reason": f"{primary} mapped to {memory_scope}",
        "interpretation": {
            "summary": _summary(primary, normalized),
            "explicitness": "explicit",
            "evidence": [normalized],
            "ambiguity_note": None,
        },
        "revision_intent": {
            "actions": list(dict.fromkeys(actions)),
            "preserve_original_direction": primary not in {"prefers_goal_type"},
            "allow_scope_expansion": False,
            "target_goal_type": "coding" if "coding" in preference_delta["preferred_goal_types"] else None,
            "target_output_form": None,
        },
        "constraints_delta": constraints_delta,
        "preference_delta": preference_delta,
        "quality_delta": quality_delta,
        "difficulty_delta": difficulty_delta,
        "direction_delta": direction_delta,
        "requires_goal_revision": True,
        "requires_memory_update": memory_scope != "current_day",
        "memory_update_suggestion": [],
        "confidence": 0.9 if primary != "other" else 0.55,
        "safety_flags": {
            "needs_clarification": False,
            "missing_current_goal": False,
            "conflicts_with_profile": False,
            "out_of_workday": False,
            "cannot_revise_without_expanding_scope": False,
        },
    }


class MockGoalRevisionAgent:
    def revise(self, current_goal: dict[str, Any], feedback_signal: dict[str, Any]) -> dict[str, Any]:
        revised = json.loads(json.dumps(current_goal, ensure_ascii=False))
        actions = set(feedback_signal["revision_intent"]["actions"])
        constraints = feedback_signal["constraints_delta"]
        difficulty = feedback_signal["difficulty_delta"]

        if "convert_goal_type" in actions:
            revised["goal_type"] = "coding"
            revised["main_goal"] = "实现一个与今日目标相关的可运行代码切片"
            revised["completion_criteria"] = [
                "完成一处可运行的代码改动",
                "用一次本地验证确认改动有效",
            ]
            revised["minimum_acceptable_result"] = "至少提交一个可运行的最小代码改动。"
            revised["context_used"]["primary_driver"] = "tomorrow_direction"
            revised["context_used"]["continuity_note"] = "用户反馈更想写代码，因此改为实现型交付物。"

        if "shrink_scope" in actions or "shorten_time" in actions:
            max_minutes = constraints.get("available_minutes") or difficulty.get("estimated_minutes_max")
            current_minutes = int(revised["estimated_minutes"])
            if max_minutes is not None:
                revised["estimated_minutes"] = max(30, min(current_minutes, int(max_minutes)))
            else:
                revised["estimated_minutes"] = max(30, min(current_minutes, int(current_minutes * 0.7)))
            revised["difficulty"] = max(1, int(revised["difficulty"]) - 1)
            revised["completion_criteria"] = revised["completion_criteria"][:2]
            revised["main_goal"] = _prefix_once(revised["main_goal"], "缩小范围：")
            revised["minimum_acceptable_result"] = "先完成一个更小但可检查的核心成果。"
            revised["context_used"]["difficulty_reason"] = "用户反馈目标范围或可用时间受限，因此降低耗时、难度和完成标准数量。"

        if "make_more_specific" in actions or "require_deliverable" in actions:
            revised["main_goal"] = _prefix_once(revised["main_goal"], "交付明确成果：")
            revised["completion_criteria"] = [
                "产出一个可命名、可打开或可运行的交付物",
                "写明交付物满足今日目标的验收条件",
            ]
            revised["minimum_acceptable_result"] = "至少留下一个可检查的文件、代码改动或说明清单。"

        revised["rationale"] = (
            f"根据用户反馈“{feedback_signal['normalized_feedback']}”修正今日目标。"
            "新版目标保持单一主目标，并限制在今天可完成的范围内。"
        )
        revised["stretch_challenge"] = "完成修正版最低成果后，再补充一条验证记录。"
        revised["do_not_do_today"] = list(
            dict.fromkeys(revised["do_not_do_today"] + ["不要把本次反馈扩展成新的任务清单"])
        )[:4]
        revised["main_goal"] = _compact_text(revised["main_goal"], 120)
        revised["context_used"]["tomorrow_direction_handling"] = "partially_used"
        return revised


def _goal_revision_messages(
    current_goal: dict[str, Any],
    feedback_signal: dict[str, Any],
    soul: str,
) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot Goal Revision Agent. Return only valid json matching daily_goal.v1.
Revise the current goal according to the structured feedback signal. Do not expand scope.
Use concise Chinese for user-facing fields.
"""
    user = {
        "task": "Revise today's active goal.",
        "current_goal": current_goal,
        "feedback_signal": feedback_signal,
        "required_behavior": [
            "preserve one main goal",
            "respect time and scope constraints",
            "include feedback in rationale",
            "return only JSON",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _resolve_goal_record(connection, feedback_date: date, requested_goal_id: Any) -> dict[str, Any]:
    if requested_goal_id not in (None, ""):
        try:
            goal_id = int(requested_goal_id)
        except (TypeError, ValueError) as exc:
            raise GoalFeedbackValidationError("goal_id 必须是整数。") from exc
        daily_goal = repo.get_daily_goal(connection, goal_id)
        if daily_goal is None:
            raise GoalFeedbackValidationError("指定的 goal_id 不存在。")
        if daily_goal["goal_date"] != feedback_date.isoformat():
            raise GoalFeedbackValidationError("goal_id 与反馈日期不一致。")
        active_version = (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal["active_version_id"] is not None
            else None
        )
        if active_version is None:
            raise GoalFeedbackValidationError("当前目标缺少 active version。")
        return {"daily_goal": daily_goal, "active_version": active_version}

    goal_record = repo.get_goal_with_active_version_by_date(connection, feedback_date.isoformat())
    if goal_record is None or goal_record.get("active_version") is None:
        raise GoalFeedbackValidationError("提交反馈前需要先获取今日 active goal。")
    return goal_record


def _parse_feedback_date(value: Any, default_date: date) -> date:
    if value in (None, ""):
        return default_date
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise GoalFeedbackValidationError("date 必须是 YYYY-MM-DD。") from exc


def _extract_minutes(text: str) -> int | None:
    minute_match = re.search(r"(\d{1,3})\s*(分钟|分|min|minutes)", text, flags=re.IGNORECASE)
    if minute_match:
        return int(minute_match.group(1))
    hour_match = re.search(r"(\d{1,2})\s*(小时|h|hour)", text, flags=re.IGNORECASE)
    if hour_match:
        return int(hour_match.group(1)) * 60
    return None


def _memory_scope(text: str, primary: str) -> str:
    if _has_any(text, ["以后", "长期", "每次", "一直", "长期偏好"]):
        return "long_term"
    if primary in {"time_limit", "scope_too_large", "goal_too_vague"}:
        return "current_day"
    if primary == "prefers_goal_type":
        return "short_term"
    return "current_day"


def _summary(primary: str, text: str) -> str:
    mapping = {
        "time_limit": "用户反馈今天可用时间受限，需要缩短目标。",
        "scope_too_large": "用户反馈目标过大，需要缩小范围。",
        "goal_too_vague": "用户反馈目标不够具体，需要明确交付物。",
        "prefers_goal_type": "用户反馈更偏好实现型或代码型任务。",
        "other": "用户反馈需要对当前目标做保守微调。",
    }
    return mapping.get(primary, "用户反馈需要修正当前目标。")


def _db_feedback_type(signal: dict[str, Any]) -> str:
    primary = signal["primary_feedback_type"]
    if primary == "time_limit":
        return "day_constraint"
    if primary == "prefers_goal_type":
        return "short_term_preference"
    if primary in {"scope_too_large", "goal_too_vague"}:
        return "quality_issue"
    return "other"


def _db_affected_scope(signal: dict[str, Any]) -> str:
    scope = signal.get("memory_scope")
    if scope in {"short_term", "next_3_7_days"}:
        return "next_3_7_days"
    if scope == "long_term":
        return "long_term"
    return "today"


def _memory_action(signal: dict[str, Any]) -> str:
    if not signal.get("requires_memory_update"):
        return "none"
    if signal.get("memory_scope") == "long_term":
        return "update_long_term_preference"
    if signal.get("memory_scope") in {"short_term", "next_3_7_days"}:
        return "update_short_term_preference"
    return "none"


def _has_any(text: str, needles: list[str]) -> bool:
    normalized = text.lower()
    return any(needle.lower() in normalized for needle in needles)


def _prefix_once(text: str, prefix: str) -> str:
    return text if text.startswith(prefix) else f"{prefix}{text}"


def _compact_text(value: str, max_chars: int) -> str:
    return " ".join(value.split())[:max_chars]
