from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


DELIVERABLE_WORDS = [
    "文档",
    "清单",
    "接口",
    "测试",
    "页面",
    "草稿",
    "PR",
    "脚本",
    "表格",
    "记录",
    "设计稿",
    "示例",
    "代码",
    "改动",
    "交付物",
    "产出",
    "报告",
    "闭环",
    "结果",
    "schema",
    "api",
]
VAGUE_WORDS = ["继续学习", "继续研究", "持续推进", "优化完善", "提升能力", "研究一下", "看看"]
OVERSIZED_WORDS = ["全部", "整个", "全面", "所有", "完整系统", "端到端", "本周", "这周", "下周", "这个月", "长期"]
MULTI_GOAL_MARKERS = ["；", ";", "1.", "2.", "\n-", "\n1", "同时", "并且", "以及"]


@dataclass(frozen=True)
class GoalQualityResult:
    passed: bool
    score: int
    decision: str
    failed_rules: list[dict[str, str]]
    strengths: list[str]
    rewrite_instruction: str
    safe_to_rewrite: bool
    safe_to_degrade: bool
    user_visible_message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "decision": self.decision,
            "failed_rules": self.failed_rules,
            "strengths": self.strengths,
            "rewrite_instruction": self.rewrite_instruction,
            "safe_to_rewrite": self.safe_to_rewrite,
            "safe_to_degrade": self.safe_to_degrade,
            "user_visible_message": self.user_visible_message,
        }


@dataclass(frozen=True)
class GoalQualityGateResult:
    goal: dict[str, Any]
    review: dict[str, Any]
    quality_status: str


def ensure_goal_quality(goal: dict[str, Any], *, flow: str) -> GoalQualityGateResult:
    first_review = review_goal(goal, flow=flow)
    if first_review.passed:
        return GoalQualityGateResult(goal=goal, review=first_review.as_dict(), quality_status="passed")

    rewritten = rewrite_goal(goal, first_review)
    second_review = review_goal(rewritten, flow=flow)
    status = "rewritten_passed" if second_review.passed else "degraded"
    if not second_review.passed:
        rewritten = build_degraded_goal(goal)
        second_review = review_goal(rewritten, flow=flow)
    return GoalQualityGateResult(goal=rewritten, review=second_review.as_dict(), quality_status=status)


def review_goal(goal: dict[str, Any], *, flow: str) -> GoalQualityResult:
    failures: list[dict[str, str]] = []
    strengths: list[str] = []
    required = [
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
    ]
    missing = [name for name in required if _is_empty(goal.get(name))]
    if missing:
        failures.append(_failure("R00", "blocker", f"缺少必填字段: {', '.join(missing)}", ",".join(missing)))
    else:
        strengths.append("必填字段完整")

    main_goal = str(goal.get("main_goal") or "")
    criteria = goal.get("completion_criteria")
    estimated_minutes = goal.get("estimated_minutes")
    difficulty = goal.get("difficulty")
    minimum = str(goal.get("minimum_acceptable_result") or "")
    do_not = goal.get("do_not_do_today")

    if not isinstance(criteria, list) or not 2 <= len(criteria) <= 5:
        failures.append(_failure("R04", "blocker", "完成标准必须是 2-5 条。", str(criteria)))
    else:
        strengths.append("完成标准数量合规")
        invalid_criteria = [
            str(item)
            for item in criteria
            if not isinstance(item, str) or len(item.strip()) < 8 or len(item) > 140
        ]
        if invalid_criteria:
            failures.append(
                _failure("R03", "blocker", "每条完成标准必须是 8-140 字的可检查描述。", ";".join(invalid_criteria))
            )

    if not isinstance(estimated_minutes, int) or not 30 <= estimated_minutes <= 150:
        failures.append(_failure("R05", "blocker", "预计耗时必须在 30-150 分钟。", str(estimated_minutes)))
    else:
        strengths.append("预计耗时合规")

    if not isinstance(difficulty, int) or difficulty < 1 or difficulty > 5:
        failures.append(_failure("R06", "blocker", "难度必须是 1-5 的整数。", str(difficulty)))
    else:
        strengths.append("难度评分合规")

    if len(main_goal.strip()) < 8 or len(main_goal) > 120:
        failures.append(_failure("R02", "blocker", "主目标长度不合规。", main_goal))

    if _has_any(main_goal, MULTI_GOAL_MARKERS):
        failures.append(_failure("R01", "blocker", "主目标疑似包含多个并列目标。", main_goal))

    if _has_any(main_goal, OVERSIZED_WORDS):
        failures.append(_failure("R11", "blocker", "主目标疑似把跨天范围包装成今日目标。", main_goal))

    if _has_any(main_goal, VAGUE_WORDS) and not _has_deliverable(main_goal):
        failures.append(_failure("R12", "blocker", "主目标过虚且缺少可交付物。", main_goal))

    if not minimum or minimum.strip() == main_goal.strip() or not _has_deliverable(minimum):
        failures.append(_failure("R07", "blocker", "最低可接受成果必须是更小的可见产出。", minimum))

    if not isinstance(do_not, list) or len(do_not) < 1:
        failures.append(_failure("R09", "major", "今天不要做不能为空。", str(do_not)))

    if flow == "revision":
        rationale = str(goal.get("rationale") or "")
        if "反馈" not in rationale and "修正" not in rationale:
            failures.append(_failure("R14", "major", "修正目标需要体现用户反馈。", rationale))

    blocker_count = sum(1 for item in failures if item["severity"] == "blocker")
    major_count = sum(1 for item in failures if item["severity"] == "major")
    score = max(0, 100 - blocker_count * 25 - major_count * 10)
    passed = blocker_count == 0 and score >= 85
    decision = "pass" if passed else "rewrite_required"
    instruction = "；".join(item["auto_fix_hint"] for item in failures) or "目标质量合格。"
    return GoalQualityResult(
        passed=passed,
        score=score,
        decision=decision,
        failed_rules=failures,
        strengths=strengths,
        rewrite_instruction=instruction,
        safe_to_rewrite=True,
        safe_to_degrade=True,
        user_visible_message="目标质量合格。" if passed else "目标质量未达标，系统已尝试缩小并明确交付物。",
    )


def rewrite_goal(goal: dict[str, Any], review: GoalQualityResult) -> dict[str, Any]:
    rewritten = copy.deepcopy(goal)
    failures = {item["id"] for item in review.failed_rules}
    rewritten.setdefault("schema_version", "daily_goal.v1")
    rewritten.setdefault("completion_criteria", [])
    rewritten.setdefault("do_not_do_today", [])
    rewritten.setdefault("growth_tags", ["daypilot_mvp", "goal_quality"])
    rewritten.setdefault(
        "context_used",
        {
            "primary_driver": "agent_decision",
            "tomorrow_direction_handling": "partially_used",
            "continuity_note": "Goal Critic 自动修正后保留今日目标主线。",
            "difficulty_reason": "Goal Critic 将目标缩小到 MVP 可执行范围。",
        },
    )

    rewritten["estimated_minutes"] = _clamp_int(rewritten.get("estimated_minutes") or 60, 30, 150)
    rewritten["difficulty"] = _clamp_int(rewritten.get("difficulty") or 2, 1, 5)
    if failures & {"R01", "R11"}:
        rewritten["main_goal"] = _prefix_once(_strip_oversized_words(str(rewritten.get("main_goal") or "")), "缩小范围：")
        rewritten["estimated_minutes"] = min(rewritten["estimated_minutes"], 90)
        rewritten["completion_criteria"] = _first_criteria(rewritten.get("completion_criteria"), 3)

    if failures & {"R02", "R12"}:
        rewritten["main_goal"] = "交付明确成果：产出一份今日目标质量改进记录"
        rewritten["completion_criteria"] = [
            "产出一个可命名、可打开或可运行的交付物",
            "记录该交付物满足今日目标的验收条件",
        ]

    rewritten["completion_criteria"] = _first_criteria(rewritten.get("completion_criteria"), 5)
    while len(rewritten["completion_criteria"]) < 2:
        rewritten["completion_criteria"].append("记录一个可人工检查的验收结果")

    if "R07" in failures or not _has_deliverable(str(rewritten.get("minimum_acceptable_result") or "")):
        rewritten["minimum_acceptable_result"] = "留下一份可复查的最小文档、代码改动或记录。"

    if not rewritten.get("stretch_challenge"):
        rewritten["stretch_challenge"] = "完成最低成果后，补充一个测试样例或风险点。"

    if not isinstance(rewritten.get("do_not_do_today"), list) or not rewritten["do_not_do_today"]:
        rewritten["do_not_do_today"] = ["不要扩展到完整系统或跨模块联调"]

    rewritten["main_goal"] = _compact_text(str(rewritten["main_goal"]), 120)
    rewritten["rationale"] = _compact_text(
        str(rewritten.get("rationale") or "Goal Critic 已将目标改写为今日可完成的可交付目标。"),
        360,
    )
    return rewritten


def build_degraded_goal(goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "daily_goal.v1",
        "goal_date": goal.get("goal_date") or "2026-06-08",
        "main_goal": "围绕 DayPilot 当前目标产出一份最小可交付记录",
        "rationale": "候选目标连续未通过质量审查，因此降级为更小、更稳、当天可完成的交付记录。",
        "completion_criteria": [
            "列出 3 个关键点或待办项",
            "标出 1 个今天不处理的范围",
        ],
        "estimated_minutes": 45,
        "difficulty": 2,
        "minimum_acceptable_result": "留下一份可复查的一页记录。",
        "stretch_challenge": "补充一个测试样例或风险点。",
        "do_not_do_today": ["不要扩展到完整实现或跨模块联调"],
        "goal_type": "documentation",
        "growth_tags": ["daypilot_mvp", "goal_quality"],
        "context_used": {
            "primary_driver": "agent_decision",
            "tomorrow_direction_handling": "partially_used",
            "continuity_note": "Goal Critic 降级为最小可交付目标。",
            "difficulty_reason": "降级目标固定为低风险 45 分钟范围。",
        },
    }


def _failure(rule_id: str, severity: str, reason: str, evidence: str) -> dict[str, str]:
    return {
        "id": rule_id,
        "severity": severity,
        "reason": reason,
        "evidence": evidence,
        "auto_fix_hint": reason,
    }


def _first_criteria(criteria: Any, limit: int) -> list[str]:
    if not isinstance(criteria, list):
        return []
    result = []
    for item in criteria:
        text = str(item).strip()
        if not text:
            continue
        if len(text) < 8:
            text = f"{text}并记录验收结果"
        result.append(text[:140])
        if len(result) >= limit:
            break
    return result


def _has_deliverable(text: str) -> bool:
    return _has_any(text, DELIVERABLE_WORDS)


def _strip_oversized_words(text: str) -> str:
    result = text
    for word in OVERSIZED_WORDS:
        result = result.replace(word, "")
    return result.strip(" ，；。") or "完成一个今日可检查交付物"


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _prefix_once(text: str, prefix: str) -> str:
    return text if text.startswith(prefix) else f"{prefix}{text}"


def _compact_text(value: str, max_chars: int) -> str:
    return " ".join(value.split())[:max_chars]


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text.lower() for needle in needles)
