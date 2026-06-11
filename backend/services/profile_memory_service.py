from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import default_backup_dir
from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH, load_soul_context


PROMPT_VERSION_MOCK = "profile_memory_v1_mock"
PROMPT_VERSION_DEEPSEEK = "profile_memory_v1_deepseek"
MOCK_MODEL_NAME = "mock-profile-memory-adapter"
PROFILE_MEMORY_BACKUP_DIR = default_backup_dir()

PROFILE_STATUS_APPLIED = "applied"
PROFILE_STATUS_SKIPPED = "skipped"
PROFILE_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ProfileMemoryUpdateResult:
    payload: dict[str, Any]


class ProfileMemoryUpdateError(RuntimeError):
    """Raised when feedback profile memory cannot be analyzed or persisted."""


def sync_profile_memory_to_soul(
    db_path: str | Path,
    *,
    soul_path: str | Path = SOUL_PATH,
) -> Path:
    connection = initialize_database(db_path)
    try:
        profile = repo.get_user_profile(connection)
        if profile is None:
            raise ProfileMemoryUpdateError("user_profile_not_found")
        return _sync_soul_profile_sections(
            Path(soul_path),
            goal_preferences=dict(profile.get("goal_preferences") or {}),
            avoid_patterns=_string_list(profile.get("avoid_patterns")),
        )
    finally:
        connection.close()


def apply_profile_memory_from_feedback(
    db_path: str | Path,
    feedback_message_id: int,
    feedback_signal: dict[str, Any],
    *,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
) -> ProfileMemoryUpdateResult:
    try:
        context = _build_profile_memory_context(db_path, feedback_message_id, feedback_signal, Path(soul_path))
        llm_result = generate_json_with_fallback(
            task_name="profile_memory_update",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda soul: _profile_memory_messages(context, soul),
            mock_generate=lambda: MockProfileMemoryAdapter().generate(context),
            validator=_validate_profile_memory_output,
            settings=settings,
            soul_path=soul_path,
        )
        output = _normalize_profile_memory_output(llm_result.output)
    except Exception as exc:  # noqa: BLE001 - goal revision must remain successful
        return ProfileMemoryUpdateResult(
            {
                "status": PROFILE_STATUS_FAILED,
                "applied_items_count": 0,
                "soul_synced": False,
                "reason": _safe_error(exc),
                "fallback_reason": None,
            }
        )

    try:
        payload = _apply_profile_memory_output(
            db_path,
            context=context,
            output=output,
            llm_metadata=llm_result.metadata,
            soul_path=Path(soul_path),
        )
    except Exception as exc:  # noqa: BLE001 - profile memory failures must not block feedback
        return ProfileMemoryUpdateResult(
            {
                "status": PROFILE_STATUS_FAILED,
                "applied_items_count": 0,
                "soul_synced": False,
                "reason": _safe_error(exc),
                "fallback_reason": llm_result.metadata.get("fallback_reason"),
            }
        )
    return ProfileMemoryUpdateResult(payload)


class MockProfileMemoryAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        message = str(context["feedback_message"]["raw_message"] or "").strip()
        parsed = _fallback_profile_memory_output(message)
        if parsed is None:
            raise ProfileMemoryUpdateError("fallback_could_not_parse_profile_memory")
        return parsed


def _build_profile_memory_context(
    db_path: str | Path,
    feedback_message_id: int,
    feedback_signal: dict[str, Any],
    soul_path: Path,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        feedback_message = repo.get_feedback_message(connection, feedback_message_id)
        if feedback_message is None:
            raise ProfileMemoryUpdateError("feedback_message_not_found")
        daily_goal = repo.get_daily_goal(connection, int(feedback_message["daily_goal_id"]))
        if daily_goal is None:
            raise ProfileMemoryUpdateError("daily_goal_not_found")
        active_version = (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal.get("active_version_id") is not None
            else None
        )
        profile = repo.get_user_profile(connection)
        if profile is None:
            raise ProfileMemoryUpdateError("user_profile_not_found")
        goal_date = str(daily_goal["goal_date"])
        soul = load_soul_context(soul_path)
        return {
            "feedback_message": feedback_message,
            "feedback_signal": feedback_signal,
            "daily_goal": daily_goal,
            "active_version": active_version or {},
            "user_profile": profile,
            "goal_preferences": dict(profile.get("goal_preferences") or {}),
            "avoid_patterns": _string_list(profile.get("avoid_patterns")),
            "recent_feedback_messages": repo.list_recent_feedback_messages(connection, goal_date, limit=10),
            "recent_profile_memory_events": repo.list_recent_profile_memory_events(connection, limit=10),
            "soul_loaded": soul.loaded,
            "soul_path": soul.path,
            "soul_excerpt": soul.content[:5000],
        }
    finally:
        connection.close()


def _profile_memory_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot profile memory distillation agent.
Return exactly one valid JSON object. Do not include Markdown fences.
Extract only stable user-profile information from the latest goal-feedback message.
Allowed updates: stable user preferences, avoid patterns, and time/scope planning rules.
Do not update long-term direction, project lists, project progress, today's temporary mood, or one-off constraints.
If the feedback is only a one-time constraint, put it in ignored_items and leave update lists empty.
Use concise Chinese strings for all extracted items.
"""
    user = {
        "task": "Decide which stable profile memories should be saved after this goal feedback revision.",
        "required_json_fields": [
            "preference_items",
            "avoid_items",
            "time_scope_rules",
            "ignored_items",
            "reason",
            "confidence",
        ],
        "rules": [
            "No confidence threshold: legal output will be applied.",
            "Only return durable profile facts that should influence future goals.",
            "Do not save one-off phrases like today only has 30 minutes.",
            "Do not mention or modify projects.",
            "Avoid duplicates already present in goal_preferences or SOUL.md.",
        ],
        "latest_feedback": context["feedback_message"],
        "feedback_signal": context["feedback_signal"],
        "current_goal_preferences": context["goal_preferences"],
        "current_avoid_patterns": context["avoid_patterns"],
        "recent_feedback_messages": context["recent_feedback_messages"],
        "recent_profile_memory_events": context["recent_profile_memory_events"],
        "active_goal_version": context["active_version"],
        "soul_excerpt": context["soul_excerpt"],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _apply_profile_memory_output(
    db_path: str | Path,
    *,
    context: dict[str, Any],
    output: dict[str, Any],
    llm_metadata: dict[str, Any],
    soul_path: Path,
) -> dict[str, Any]:
    old_preferences = dict(context["goal_preferences"])
    old_avoid_patterns = _string_list(context["avoid_patterns"])
    new_preferences = dict(old_preferences)

    existing_stable = _string_list(new_preferences.get("stable_preferences"))
    existing_avoid = _string_list(new_preferences.get("avoid_patterns"))
    existing_time_rules = _string_list(new_preferences.get("time_scope_rules"))

    stable_preferences, added_preferences = _merge_unique(existing_stable, output["preference_items"])
    preference_avoid, added_avoid = _merge_unique(existing_avoid, output["avoid_items"])
    time_scope_rules, added_time_rules = _merge_unique(existing_time_rules, output["time_scope_rules"])
    profile_avoid_patterns, _ = _merge_unique(old_avoid_patterns, output["avoid_items"])

    new_items = added_preferences + added_avoid + added_time_rules
    applied_items_count = len(new_items)
    if applied_items_count:
        new_preferences["stable_preferences"] = stable_preferences
        new_preferences["avoid_patterns"] = preference_avoid
        new_preferences["time_scope_rules"] = time_scope_rules
        new_preferences["updated_from_feedback_at"] = _now_text()
        applied = True
        status = PROFILE_STATUS_APPLIED
        reason = output["reason"] or "stable profile memory applied"
    else:
        applied = False
        status = PROFILE_STATUS_SKIPPED
        reason = output["reason"] or "no stable profile memory extracted"

    soul_backup: Path | None = None
    soul_sync_error: str | None = None
    soul_sync_retry_job_id: int | None = None
    connection = initialize_database(db_path)
    try:
        with connection:
            profile = repo.get_user_profile(connection)
            if profile is None:
                raise ProfileMemoryUpdateError("user_profile_not_found")
            if applied:
                repo.update_user_profile(
                    connection,
                    int(profile["id"]),
                    goal_preferences=new_preferences,
                    avoid_patterns=profile_avoid_patterns,
                )
            event_id = repo.create_profile_memory_event(
                connection,
                feedback_message_id=int(context["feedback_message"]["id"]),
                daily_goal_id=int(context["daily_goal"]["id"]),
                raw_feedback=context["feedback_message"]["raw_message"],
                preference_items=output["preference_items"],
                avoid_items=output["avoid_items"],
                time_scope_rules=output["time_scope_rules"],
                ignored_items=output["ignored_items"],
                previous_goal_preferences=old_preferences,
                new_goal_preferences=new_preferences if applied else old_preferences,
                soul_backup_path=str(soul_backup) if soul_backup else None,
                confidence=output["confidence"],
                applied=1 if applied else 0,
                reason=reason,
                llm_metadata=llm_metadata,
                raw_output=output,
            )
    finally:
        connection.close()

    if applied:
        try:
            soul_backup = _sync_soul_profile_sections(
                soul_path,
                goal_preferences=new_preferences,
                avoid_patterns=profile_avoid_patterns,
            )
            connection = initialize_database(db_path)
            try:
                with connection:
                    repo.update_profile_memory_event(
                        connection,
                        event_id,
                        soul_backup_path=str(soul_backup),
                    )
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001 - DB memory is the source of truth
            soul_sync_error = _safe_error(exc)
            from backend.services.soul_sync_service import enqueue_soul_sync_retry

            soul_sync_retry_job_id = enqueue_soul_sync_retry(
                db_path,
                job_type="profile_memory",
                source_table="profile_memory_events",
                source_id=event_id,
                payload={
                    "profile_id": 1,
                    "profile_memory_event_id": event_id,
                },
                error=soul_sync_error,
            )

    return {
        "status": status,
        "applied_items_count": applied_items_count,
        "soul_synced": bool(soul_backup),
        "reason": reason,
        "fallback_reason": llm_metadata.get("fallback_reason"),
        "profile_memory_event_id": event_id,
        "ignored_items": output["ignored_items"],
        "soul_sync_queued": soul_sync_retry_job_id is not None,
        "soul_sync_retry_job_id": soul_sync_retry_job_id,
        "soul_sync_error": soul_sync_error,
    }


def _validate_profile_memory_output(output: dict[str, Any]) -> None:
    _normalize_profile_memory_output(output)


def _normalize_profile_memory_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("profile_memory_output_not_object")
    required = [
        "preference_items",
        "avoid_items",
        "time_scope_rules",
        "ignored_items",
        "reason",
        "confidence",
    ]
    missing = [field for field in required if field not in output]
    if missing:
        raise ValueError(f"profile_memory_missing_fields:{','.join(missing)}")

    return {
        "preference_items": _clean_output_items(output["preference_items"]),
        "avoid_items": _clean_output_items(output["avoid_items"]),
        "time_scope_rules": _clean_output_items(output["time_scope_rules"]),
        "ignored_items": _clean_output_items(output["ignored_items"]),
        "reason": str(output.get("reason") or "").strip(),
        "confidence": _clamp_float(output.get("confidence"), 0.0, 1.0),
    }


def _fallback_profile_memory_output(message: str) -> dict[str, Any] | None:
    normalized = " ".join(str(message or "").split())
    preference_items: list[str] = []
    avoid_items: list[str] = []
    time_scope_rules: list[str] = []
    ignored_items: list[str] = []

    if _looks_like_one_time_time_limit(normalized):
        ignored_items.append(normalized[:160])

    for prefix in ["我更喜欢", "以后都", "每次都"]:
        for phrase in _segments_after_prefix(normalized, prefix):
            if not phrase:
                continue
            if _is_avoid_phrase(phrase):
                avoid_items.append(_normalize_avoid_phrase(phrase))
            elif _is_time_scope_phrase(phrase):
                time_scope_rules.append(_normalize_preference_phrase(prefix, phrase))
            else:
                preference_items.append(_normalize_preference_phrase(prefix, phrase))

    for prefix in ["不要再", "以后不要", "以后都不要"]:
        for phrase in _segments_after_prefix(normalized, prefix):
            if phrase:
                avoid_items.append(_normalize_avoid_phrase(phrase))

    preference_items = _dedupe_texts(preference_items)
    avoid_items = _dedupe_texts(avoid_items)
    time_scope_rules = _dedupe_texts(time_scope_rules)
    ignored_items = _dedupe_texts(ignored_items)

    if not preference_items and not avoid_items and not time_scope_rules and not ignored_items:
        return None
    return {
        "preference_items": preference_items,
        "avoid_items": avoid_items,
        "time_scope_rules": time_scope_rules,
        "ignored_items": ignored_items,
        "reason": "mock fallback parsed explicit stable profile wording."
        if preference_items or avoid_items or time_scope_rules
        else "feedback looked like a one-time constraint, so it was ignored for stable memory.",
        "confidence": 0.55 if preference_items or avoid_items or time_scope_rules else 0.4,
    }


def _sync_soul_profile_sections(
    soul_path: Path,
    *,
    goal_preferences: dict[str, Any],
    avoid_patterns: list[str],
) -> Path:
    text = soul_path.read_text(encoding="utf-8")
    stable_preferences = _string_list(goal_preferences.get("stable_preferences"))
    preference_avoid = _string_list(goal_preferences.get("avoid_patterns"))
    time_rules = _string_list(goal_preferences.get("time_scope_rules"))

    preference_items = _merge_for_soul(
        _extract_section_bullets(text, "## 用户偏好", "## 避免事项"),
        stable_preferences,
    )
    avoid_items = _merge_for_soul(
        _extract_section_bullets(text, "## 避免事项", "## 时间预算与目标数量"),
        avoid_patterns,
        preference_avoid,
    )
    time_items = _merge_for_soul(
        _extract_section_bullets(text, "## 时间预算与目标数量", "## 每日目标原则"),
        time_rules,
    )

    backup_path = _backup_soul(soul_path)
    next_text = text
    next_text = _replace_section(
        next_text,
        "## 用户偏好",
        "## 避免事项",
        _render_user_preferences_section(preference_items),
    )
    next_text = _replace_section(
        next_text,
        "## 避免事项",
        "## 时间预算与目标数量",
        _render_avoid_items_section(avoid_items),
    )
    next_text = _replace_section(
        next_text,
        "## 时间预算与目标数量",
        "## 每日目标原则",
        _render_time_scope_section(time_items),
    )
    soul_path.write_text(next_text, encoding="utf-8")
    return backup_path


def _extract_section_bullets(text: str, start_marker: str, end_marker: str) -> list[str]:
    section = _section_text(text, start_marker, end_marker)
    bullets: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return _dedupe_texts(bullets)


def _section_text(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end <= start:
        raise ProfileMemoryUpdateError(f"SOUL.md section markers not found: {start_marker} -> {end_marker}")
    return text[start:end]


def _replace_section(text: str, start_marker: str, end_marker: str, rendered_section: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end <= start:
        raise ProfileMemoryUpdateError(f"SOUL.md section markers not found: {start_marker} -> {end_marker}")
    return text[:start] + rendered_section.rstrip() + "\n\n" + text[end:]


def _render_user_preferences_section(items: list[str]) -> str:
    lines = ["## 用户偏好", "", "用户更喜欢：", ""]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).rstrip()


def _render_avoid_items_section(items: list[str]) -> str:
    lines = ["## 避免事项", "", "生成目标时要避免：", ""]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).rstrip()


def _render_time_scope_section(items: list[str]) -> str:
    lines = ["## 时间预算与目标数量"]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).rstrip()


def _backup_soul(soul_path: Path) -> Path:
    PROFILE_MEMORY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = PROFILE_MEMORY_BACKUP_DIR / f"SOUL_{stamp}.md"
    shutil.copy2(soul_path, backup_path)
    return backup_path


def _merge_unique(existing: list[str], additions: list[str]) -> tuple[list[str], list[str]]:
    merged = _dedupe_texts(existing)
    seen = {_normalize_key(item) for item in merged}
    added: list[str] = []
    for item in _dedupe_texts(additions):
        key = _normalize_key(item)
        if not key or key in seen:
            continue
        merged.append(item)
        added.append(item)
        seen.add(key)
    return merged, added


def _merge_for_soul(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in _dedupe_texts(group):
            key = _normalize_key(item)
            if not key or key in seen:
                continue
            merged.append(item)
            seen.add(key)
    return merged


def _clean_output_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("profile_memory_items_must_be_lists")
    items: list[str] = []
    for raw_item in value:
        if isinstance(raw_item, dict):
            raw_item = raw_item.get("text") or raw_item.get("item") or raw_item.get("value")
        text = _compact_text(raw_item, 180)
        if text:
            items.append(text)
    return _dedupe_texts(items)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe_texts(_compact_text(item, 180) for item in value)
    if isinstance(value, str) and value.strip():
        return [_compact_text(value, 180)]
    return []


def _dedupe_texts(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _compact_text(value, 180)
        key = _normalize_key(text)
        if not text or not key or key in seen:
            continue
        result.append(text)
        seen.add(key)
    return result


def _compact_text(value: Any, max_chars: int) -> str:
    return " ".join(str(value or "").split()).strip()[:max_chars]


def _normalize_key(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[\s，,。；;：:、.!！?？“”\"'`（）()\[\]【】<>《》-]+", "", lowered)
    return lowered


def _looks_like_one_time_time_limit(message: str) -> bool:
    if "今天" not in message and "今日" not in message:
        return False
    return bool(re.search(r"\d{1,3}\s*(分钟|分|小时|h|hour|min)", message, flags=re.IGNORECASE))


def _segments_after_prefix(message: str, prefix: str) -> list[str]:
    segments: list[str] = []
    start = 0
    while True:
        index = message.find(prefix, start)
        if index < 0:
            break
        tail = message[index + len(prefix) :].strip(" ：:，,。；;")
        segment = re.split(r"[。；;\n]", tail, maxsplit=1)[0].strip(" ：:，,。；;")
        if segment:
            segments.append(segment)
        start = index + len(prefix)
    return segments


def _is_avoid_phrase(phrase: str) -> bool:
    return any(marker in phrase for marker in ["不要", "别", "避免", "不希望"])


def _is_time_scope_phrase(phrase: str) -> bool:
    return any(marker in phrase for marker in ["时间", "分钟", "小时", "范围", "目标数量", "塞太多", "大块时间"])


def _normalize_preference_phrase(prefix: str, phrase: str) -> str:
    cleaned = phrase.strip(" ：:，,。；;")
    if prefix == "我更喜欢":
        return cleaned
    return f"{prefix}{cleaned}"


def _normalize_avoid_phrase(phrase: str) -> str:
    cleaned = phrase.strip(" ：:，,。；;")
    for prefix in ["不要再", "以后不要", "以后都不要", "不要", "别"]:
        if cleaned.startswith(prefix):
            return cleaned
    return f"不要{cleaned}"


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
