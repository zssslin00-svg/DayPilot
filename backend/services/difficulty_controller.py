from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import mean
from typing import Any

from backend.repositories import daypilot_repository as repo


LOW_COMPLETION = 0.60
VERY_LOW_COMPLETION = 0.35
HIGH_COMPLETION = 0.85

DEFAULT_WEIGHTS = {
    "coding": 0.30,
    "documentation": 0.20,
    "design": 0.20,
    "research": 0.15,
    "review": 0.10,
    "planning": 0.05,
}


@dataclass(frozen=True)
class CompletionParseResult:
    completion_rate: float
    parser_confidence: str
    source: str
    evidence: str


@dataclass(frozen=True)
class DifficultyUpdateResult:
    ability_state: dict[str, Any]
    completion_parse_result: dict[str, Any]
    difficulty_update_event: dict[str, Any]


def parse_completion_rate(completion_text: str) -> CompletionParseResult:
    text = _normalize_text(completion_text)
    percent = re.search(r"(\d{1,3})\s*%", text)
    if percent:
        value = _clamp(float(percent.group(1)) / 100.0, 0.0, 1.0)
        return CompletionParseResult(value, "high", "explicit_percent", percent.group(0))

    fraction = re.search(r"完成\s*(\d+)\s*/\s*(\d+)|(\d+)\s*/\s*(\d+)", text)
    if fraction:
        done = int(fraction.group(1) or fraction.group(3))
        total = int(fraction.group(2) or fraction.group(4))
        value = _clamp(done / max(total, 1), 0.0, 1.0)
        return CompletionParseResult(value, "high", "explicit_fraction", fraction.group(0))

    if _has_any(text, ["没开始", "完全没做", "没有推进"]):
        return CompletionParseResult(0.0, "high", "not_started", "not started keyword")

    if _has_any(text, ["没有完成", "没完成", "未完成", "没跑通", "未能提交"]):
        rate = 0.25 if _has_any(text, ["推进", "定位", "草稿", "部分"]) else 0.10
        return CompletionParseResult(rate, "medium", "negated_completion", "negated completion")

    if _has_any(text, ["基本完成", "差不多完成", "主体完成", "核心完成"]):
        return CompletionParseResult(0.80, "medium", "mostly_done", "mostly done keyword")

    if _has_any(text, ["大部分", "主要完成"]):
        return CompletionParseResult(0.75, "medium", "majority_done", "majority keyword")

    if _has_any(text, ["一半", "部分完成", "做了一部分"]):
        return CompletionParseResult(0.50, "medium", "partial_done", "partial keyword")

    if _has_any(text, ["刚开始", "初步", "草稿", "只完成", "卡住"]):
        return CompletionParseResult(0.30, "low", "small_progress", "small progress keyword")

    if _has_any(text, ["完成", "搞定", "交付", "提交", "跑通", "写完", "合并", "发布"]):
        return CompletionParseResult(1.0, "medium", "done_keyword", "done keyword")

    if _has_any(text, ["pr", "测试", "文档", "脚本", "接口", "原型", "报告"]):
        return CompletionParseResult(0.45, "low", "artifact_only", "artifact keyword")

    return CompletionParseResult(0.50, "low", "unknown", "default estimate")


def update_ability_state_after_checkin(connection, checkin_id: int) -> DifficultyUpdateResult:
    checkin = repo.get_daily_checkin(connection, checkin_id)
    if checkin is None:
        raise ValueError("checkin_id does not exist.")

    parse_result = parse_completion_rate(str(checkin["completion_text"]))
    processor_snapshot = {
        "completion_parse_result": _parse_result_dict(parse_result),
        "source": "difficulty_controller.v1",
    }
    repo.update_daily_checkin(
        connection,
        checkin_id,
        parsed_completion_rate=parse_result.completion_rate,
        processor_snapshot=processor_snapshot,
    )
    checkin = repo.get_daily_checkin(connection, checkin_id)

    old_state = repo.get_current_ability_state(connection)
    old_difficulty = _clamp_int(
        (old_state or {}).get("target_difficulty_level")
        or (old_state or {}).get("current_difficulty")
        or 2,
        1,
        5,
    )

    history = repo.list_latest_daily_checkins_through(
        connection,
        str(checkin["checkin_date"]),
        limit=7,
    )
    rates = [
        float(item["parsed_completion_rate"])
        for item in history
        if item.get("parsed_completion_rate") is not None
    ]
    felt_values = [int(item["felt_difficulty"]) for item in history if item.get("felt_difficulty")]
    recent_3 = history[:3]
    recent_3_rates = [
        float(item["parsed_completion_rate"])
        for item in recent_3
        if item.get("parsed_completion_rate") is not None
    ]
    recent_3_felt = [int(item["felt_difficulty"]) for item in recent_3 if item.get("felt_difficulty")]

    today_rate = parse_result.completion_rate
    today_felt = int(checkin["felt_difficulty"])
    completion_streak = _count_prefix(history, lambda item: _rate(item) >= HIGH_COMPLETION)
    low_completion_streak = _count_prefix(history, lambda item: _rate(item) < LOW_COMPLETION)
    hard_streak = _count_prefix(history, lambda item: int(item["felt_difficulty"]) >= 4)
    easy_streak = _count_prefix(history, lambda item: int(item["felt_difficulty"]) <= 2)

    delta, direction, reason_codes = _decide_difficulty_delta(
        old_difficulty=old_difficulty,
        today_rate=today_rate,
        today_felt=today_felt,
        recent_3_rates=recent_3_rates,
        recent_3_felt=recent_3_felt,
        completion_streak=completion_streak,
        low_completion_streak=low_completion_streak,
        hard_streak=hard_streak,
    )
    new_difficulty = _clamp_int(old_difficulty + delta, 1, 5)
    default_minutes = _default_minutes_for_difficulty(new_difficulty, reason_codes)
    active_version = _active_version_for_checkin(connection, checkin)
    weights = _update_goal_type_weights(
        (old_state or {}).get("preferred_goal_type_weights"),
        active_version.get("goal_type") if active_version else None,
        today_rate,
        today_felt,
    )

    ability_state_id = repo.create_ability_state(
        connection,
        state_date=checkin["checkin_date"],
        source_checkin_id=checkin_id,
        current_difficulty=float(new_difficulty),
        target_difficulty_level=new_difficulty,
        recent_completion_rate=round(mean(rates), 3) if rates else today_rate,
        recent_felt_difficulty_avg=round(mean(felt_values), 3) if felt_values else today_felt,
        completion_streak=completion_streak,
        low_completion_streak=low_completion_streak,
        overload_count=hard_streak,
        underload_count=easy_streak,
        default_estimated_minutes=default_minutes,
        preferred_goal_type_weights=weights,
        short_term_preferences=(old_state or {}).get("short_term_preferences") or {},
        long_term_preferences_snapshot=(old_state or {}).get("long_term_preferences_snapshot") or {},
        avoid_patterns_snapshot=(old_state or {}).get("avoid_patterns_snapshot") or [],
        adjustment_direction=direction,
        update_reason=", ".join(reason_codes),
        is_current=1,
    )
    ability_state = repo.get_ability_state(connection, ability_state_id)
    event = {
        "old_difficulty": old_difficulty,
        "new_difficulty": new_difficulty,
        "delta": new_difficulty - old_difficulty,
        "reason_codes": reason_codes,
    }
    return DifficultyUpdateResult(
        ability_state=ability_state,
        completion_parse_result=_parse_result_dict(parse_result),
        difficulty_update_event=event,
    )


def _decide_difficulty_delta(
    *,
    old_difficulty: int,
    today_rate: float,
    today_felt: int,
    recent_3_rates: list[float],
    recent_3_felt: list[int],
    completion_streak: int,
    low_completion_streak: int,
    hard_streak: int,
) -> tuple[int, str, list[str]]:
    if today_rate < VERY_LOW_COMPLETION and today_felt == 5:
        return -1, "decrease", ["very_low_completion_too_hard"]

    if recent_3_rates and mean(recent_3_rates) < LOW_COMPLETION and recent_3_felt and mean(recent_3_felt) >= 4:
        return -1, "decrease", ["low_completion_hard_recent"]

    if low_completion_streak >= 2 and hard_streak >= 1:
        return -1, "decrease", ["low_completion_streak_hard"]

    if today_rate >= HIGH_COMPLETION and today_felt >= 4:
        return 0, "hold", ["high_completion_but_hard"]

    if today_rate < LOW_COMPLETION and today_felt <= 2:
        return 0, "change_direction", ["low_completion_easy_direction_mismatch"]

    if completion_streak >= 3 and recent_3_felt and mean(recent_3_felt) <= 2.7:
        if old_difficulty <= 2:
            return 1, "increase", ["high_completion_easy_3d"]
        return 0, "hold", ["high_completion_enable_stretch"]

    if LOW_COMPLETION <= today_rate < HIGH_COMPLETION and 2 <= today_felt <= 4:
        return 0, "hold", ["healthy_completion_hold"]

    return 0, "hold", ["insufficient_evidence_hold"]


def _update_goal_type_weights(
    old_weights: Any,
    goal_type: Any,
    today_rate: float,
    today_felt: int,
) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if isinstance(old_weights, dict):
        for key, value in old_weights.items():
            schema_key = _schema_goal_type(key)
            try:
                weights[schema_key] = float(value)
            except (TypeError, ValueError):
                continue

    current_type = _schema_goal_type(goal_type)
    if today_rate >= HIGH_COMPLETION and today_felt <= 3:
        weights[current_type] = weights.get(current_type, 0.05) + 0.03
    elif today_rate < LOW_COMPLETION and today_felt <= 2:
        weights[current_type] = weights.get(current_type, 0.05) - 0.05

    bounded = {key: _clamp(value, 0.05, 0.45) for key, value in weights.items()}
    total = sum(bounded.values()) or 1.0
    return {key: round(value / total, 4) for key, value in bounded.items()}


def _active_version_for_checkin(connection, checkin: dict[str, Any]) -> dict[str, Any] | None:
    goal = repo.get_daily_goal(connection, int(checkin["daily_goal_id"]))
    if goal is None or goal["active_version_id"] is None:
        return None
    return repo.get_goal_version(connection, int(goal["active_version_id"]))


def _default_minutes_for_difficulty(difficulty: int, reason_codes: list[str]) -> int:
    minutes = {1: 40, 2: 60, 3: 80, 4: 105, 5: 120}[difficulty]
    if any(code.startswith("low_completion") or code.startswith("very_low") for code in reason_codes):
        return max(30, minutes - 15)
    return minutes


def _parse_result_dict(parse_result: CompletionParseResult) -> dict[str, Any]:
    return {
        "completion_rate": parse_result.completion_rate,
        "parser_confidence": parse_result.parser_confidence,
        "source": parse_result.source,
        "evidence": parse_result.evidence,
    }


def _count_prefix(items: list[dict[str, Any]], predicate) -> int:
    count = 0
    for item in items:
        if not predicate(item):
            break
        count += 1
    return count


def _rate(item: dict[str, Any]) -> float:
    if item.get("parsed_completion_rate") is None:
        return 0.0
    return float(item["parsed_completion_rate"])


def _schema_goal_type(goal_type: Any) -> str:
    mapping = {
        "implementation": "coding",
        "code": "coding",
        "docs": "documentation",
        "document": "documentation",
    }
    value = str(goal_type or "coding")
    value = mapping.get(value, value)
    return value if value in DEFAULT_WEIGHTS else "coding"


def _normalize_text(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))
