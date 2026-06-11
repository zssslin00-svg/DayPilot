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
from backend.services.soul_context import SOUL_PATH


PROMPT_VERSION_MOCK = "weekly_report_memory_v1_mock"
PROMPT_VERSION_DEEPSEEK = "weekly_report_memory_v1_deepseek"
MOCK_MODEL_NAME = "mock-weekly-report-memory-adapter"
WEEKLY_REPORT_MEMORY_BACKUP_DIR = default_backup_dir()

WEEKLY_REPORT_MEMORY_KEYS = (
    "style_preferences",
    "avoid_patterns",
    "structure_preferences",
    "evidence_preferences",
    "revision_patterns",
)

MEMORY_STATUS_APPLIED = "applied"
MEMORY_STATUS_SKIPPED = "skipped"
MEMORY_STATUS_FAILED = "failed"
MEMORY_STATUS_QUEUED = "queued"


@dataclass(frozen=True)
class WeeklyReportMemoryUpdateResult:
    payload: dict[str, Any]


class WeeklyReportMemoryUpdateError(RuntimeError):
    """Raised when weekly report feedback cannot be persisted as profile memory."""


def default_weekly_report_preferences() -> dict[str, list[str]]:
    return {key: [] for key in WEEKLY_REPORT_MEMORY_KEYS}


def weekly_report_preferences_from_profile(profile: dict[str, Any] | None) -> dict[str, list[str]]:
    preferences = (profile or {}).get("goal_preferences") or {}
    if not isinstance(preferences, dict):
        return default_weekly_report_preferences()
    return normalize_weekly_report_preferences(preferences.get("weekly_report_preferences"))


def normalize_weekly_report_preferences(value: Any) -> dict[str, list[str]]:
    normalized = default_weekly_report_preferences()
    if isinstance(value, dict):
        for key in WEEKLY_REPORT_MEMORY_KEYS:
            normalized[key] = _string_list(value.get(key))
    return normalized


def apply_weekly_report_memory_from_feedback(
    db_path: str | Path,
    *,
    week_id: str,
    feedback_message: str,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
) -> WeeklyReportMemoryUpdateResult:
    message = str(feedback_message or "").strip()
    if not message:
        return WeeklyReportMemoryUpdateResult(
            {
                "status": MEMORY_STATUS_SKIPPED,
                "applied_items_count": 0,
                "reason": "empty_weekly_report_feedback",
            }
        )
    try:
        context = _build_context(db_path, week_id=week_id, feedback_message=message)
        llm_result = generate_json_with_fallback(
            task_name="weekly_report_memory_update",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda soul: _weekly_report_memory_messages(context, soul),
            mock_generate=lambda: MockWeeklyReportMemoryAdapter().generate(context),
            validator=_validate_output,
            settings=settings,
            soul_path=soul_path,
        )
        output = _normalize_output(llm_result.output)
    except Exception as exc:  # noqa: BLE001 - report feedback should still succeed
        return WeeklyReportMemoryUpdateResult(
            {
                "status": MEMORY_STATUS_FAILED,
                "applied_items_count": 0,
                "reason": _safe_error(exc),
                "fallback_reason": None,
            }
        )

    try:
        payload = _apply_output(
            db_path,
            context=context,
            output=output,
            llm_metadata=llm_result.metadata,
            soul_path=Path(soul_path),
        )
    except Exception as exc:  # noqa: BLE001 - keep report revision non-blocking
        return WeeklyReportMemoryUpdateResult(
            {
                "status": MEMORY_STATUS_FAILED,
                "applied_items_count": 0,
                "reason": _safe_error(exc),
                "fallback_reason": llm_result.metadata.get("fallback_reason"),
            }
        )
    return WeeklyReportMemoryUpdateResult(payload)


class MockWeeklyReportMemoryAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        parsed = _fallback_output(str(context.get("feedback_message") or ""))
        if parsed is None:
            raise WeeklyReportMemoryUpdateError("fallback_could_not_parse_weekly_report_feedback")
        return parsed


def _build_context(db_path: str | Path, *, week_id: str, feedback_message: str) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        profile = repo.get_user_profile(connection)
        if profile is None:
            raise WeeklyReportMemoryUpdateError("user_profile_not_found")
        weekly_report = repo.get_weekly_report_by_week(connection, week_id)
        current_report = _report_output_from_record(weekly_report)
        preferences = dict(profile.get("goal_preferences") or {})
        return {
            "week_id": week_id,
            "feedback_message": feedback_message,
            "user_profile": profile,
            "goal_preferences": preferences,
            "weekly_report_preferences": normalize_weekly_report_preferences(
                preferences.get("weekly_report_preferences")
            ),
            "current_report": current_report,
            "recent_profile_memory_events": repo.list_recent_profile_memory_events(connection, limit=8),
        }
    finally:
        connection.close()


def _weekly_report_memory_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot weekly report preference memory agent.
Return exactly one valid JSON object. Do not include Markdown fences.
Extract stable preferences about how weekly reports should be written.
Do not extract daily-goal preferences, project status, or one-off factual edits.
Use concise Chinese strings for all preference items.
"""
    user = {
        "task": "Extract reusable weekly report writing preferences from user feedback.",
        "required_json_fields": [
            "style_preferences",
            "avoid_patterns",
            "structure_preferences",
            "evidence_preferences",
            "revision_patterns",
            "ignored_items",
            "reason",
            "confidence",
        ],
        "rules": [
            "style_preferences describe tone, length, and wording style.",
            "avoid_patterns describe report patterns the user dislikes.",
            "structure_preferences describe section shape and organization.",
            "evidence_preferences describe how to cite or preserve evidence.",
            "revision_patterns describe recurring edit preferences for future feedback revisions.",
            "If unsure but the feedback is about report writing, store a concise revision_patterns item.",
            "Avoid duplicates already present in weekly_report_preferences.",
        ],
        "feedback_message": context["feedback_message"],
        "current_weekly_report": context["current_report"],
        "weekly_report_preferences": context["weekly_report_preferences"],
        "recent_profile_memory_events": context["recent_profile_memory_events"],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _apply_output(
    db_path: str | Path,
    *,
    context: dict[str, Any],
    output: dict[str, Any],
    llm_metadata: dict[str, Any],
    soul_path: Path,
) -> dict[str, Any]:
    old_preferences = dict(context["goal_preferences"])
    old_weekly_preferences = normalize_weekly_report_preferences(
        old_preferences.get("weekly_report_preferences")
    )
    new_weekly_preferences = normalize_weekly_report_preferences(old_weekly_preferences)
    added_by_key: dict[str, list[str]] = {}
    for key in WEEKLY_REPORT_MEMORY_KEYS:
        merged, added = _merge_unique(new_weekly_preferences[key], output[key])
        new_weekly_preferences[key] = merged
        added_by_key[key] = added

    applied_items_count = sum(len(items) for items in added_by_key.values())
    new_preferences = dict(old_preferences)
    if applied_items_count:
        new_preferences["weekly_report_preferences"] = new_weekly_preferences
        new_preferences["weekly_report_preferences_updated_at"] = _now_text()
        applied = True
        status = MEMORY_STATUS_APPLIED
        reason = output["reason"] or "weekly report preferences applied"
    else:
        applied = False
        status = MEMORY_STATUS_SKIPPED
        reason = output["reason"] or "no new weekly report preference extracted"

    event_id: int | None = None
    connection = initialize_database(db_path)
    try:
        with connection:
            profile = repo.get_user_profile(connection)
            if profile is None:
                raise WeeklyReportMemoryUpdateError("user_profile_not_found")
            if applied:
                repo.update_user_profile(
                    connection,
                    int(profile["id"]),
                    goal_preferences=new_preferences,
                )
            event_id = repo.create_profile_memory_event(
                connection,
                feedback_message_id=None,
                daily_goal_id=None,
                raw_feedback=context["feedback_message"],
                preference_items=output["style_preferences"] + output["structure_preferences"],
                avoid_items=output["avoid_patterns"],
                time_scope_rules=output["evidence_preferences"] + output["revision_patterns"],
                ignored_items=output["ignored_items"],
                previous_goal_preferences=old_preferences,
                new_goal_preferences=new_preferences if applied else old_preferences,
                soul_backup_path=None,
                confidence=output["confidence"],
                applied=1 if applied else 0,
                reason=reason,
                llm_metadata=llm_metadata,
                raw_output={
                    **output,
                    "memory_type": "weekly_report_preferences",
                    "week_id": context["week_id"],
                    "added_by_key": added_by_key,
                },
            )
    finally:
        connection.close()

    soul_backup_path: Path | None = None
    soul_sync_error: str | None = None
    soul_sync_retry_job_id: int | None = None
    if applied:
        try:
            soul_backup_path = _sync_weekly_report_preferences_to_soul(db_path, soul_path=soul_path)
            if event_id is not None:
                connection = initialize_database(db_path)
                try:
                    with connection:
                        repo.update_profile_memory_event(
                            connection,
                            event_id,
                            soul_backup_path=str(soul_backup_path),
                        )
                finally:
                    connection.close()
        except Exception as exc:  # noqa: BLE001 - DB is the source of truth
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
                    "memory_type": "weekly_report_preferences",
                },
                error=soul_sync_error,
            )
            status = MEMORY_STATUS_QUEUED

    return {
        "status": status,
        "applied_items_count": applied_items_count,
        "reason": reason,
        "fallback_reason": llm_metadata.get("fallback_reason"),
        "profile_memory_event_id": event_id,
        "ignored_items": output["ignored_items"],
        "weekly_report_preferences": new_weekly_preferences if applied else old_weekly_preferences,
        "soul_synced": bool(soul_backup_path),
        "soul_sync_queued": soul_sync_retry_job_id is not None,
        "soul_sync_retry_job_id": soul_sync_retry_job_id,
        "soul_sync_error": soul_sync_error,
    }


def _validate_output(output: dict[str, Any]) -> None:
    _normalize_output(output)


def _normalize_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("weekly_report_memory_output_not_object")
    required = list(WEEKLY_REPORT_MEMORY_KEYS) + ["ignored_items", "reason", "confidence"]
    missing = [field for field in required if field not in output]
    if missing:
        raise ValueError(f"weekly_report_memory_missing_fields:{','.join(missing)}")
    normalized = {key: _clean_output_items(output[key]) for key in WEEKLY_REPORT_MEMORY_KEYS}
    normalized["ignored_items"] = _clean_output_items(output["ignored_items"])
    normalized["reason"] = str(output.get("reason") or "").strip()
    normalized["confidence"] = _clamp_float(output.get("confidence"), 0.0, 1.0)
    return normalized


def _fallback_output(message: str) -> dict[str, Any] | None:
    normalized = " ".join(str(message or "").split())
    if not normalized:
        return None

    style_preferences: list[str] = []
    avoid_patterns: list[str] = []
    structure_preferences: list[str] = []
    evidence_preferences: list[str] = []
    revision_patterns: list[str] = []

    if _contains_any(normalized, ["简洁", "精简", "短一点", "少一点", "克制"]):
        style_preferences.append("周报语气要简洁克制，避免冗长铺陈。")
    if _contains_any(normalized, ["详细", "细节", "展开"]):
        style_preferences.append("周报需要保留足够细节，不要只给结论。")
    if _contains_any(normalized, ["流水账", "按天", "逐日", "周一", "周二", "周三", "周四", "周五"]):
        avoid_patterns.append("不要把周报写成按日期排列的流水账。")
        structure_preferences.append("周报应按成果、下周计划和复盘组织，而不是逐日罗列。")
    if _contains_any(normalized, ["证据", "来源", "产出", "文件", "记录"]):
        evidence_preferences.append("周报完成项要尽量保留证据来源或对应产出。")
    if _contains_any(normalized, ["下周计划", "可验收", "可验证", "结果目标"]):
        structure_preferences.append("下周计划要写成可验收的结果目标。")
    if _contains_any(normalized, ["重点", "突出", "聚焦"]):
        structure_preferences.append("周报要突出重点，不要平均铺开所有细节。")

    if not any([style_preferences, avoid_patterns, structure_preferences, evidence_preferences]):
        revision_patterns.append(f"用户曾要求这样调整周报：{_strip_sentence_end(normalized[:120])}。")
    else:
        revision_patterns.append(f"后续周报修改要参考反馈：{_strip_sentence_end(normalized[:120])}。")

    return {
        "style_preferences": _dedupe_texts(style_preferences),
        "avoid_patterns": _dedupe_texts(avoid_patterns),
        "structure_preferences": _dedupe_texts(structure_preferences),
        "evidence_preferences": _dedupe_texts(evidence_preferences),
        "revision_patterns": _dedupe_texts(revision_patterns),
        "ignored_items": [],
        "reason": "weekly report feedback captured as reusable report-writing preference.",
        "confidence": 0.65,
    }


def _report_output_from_record(record: dict[str, Any] | None) -> dict[str, list[str]]:
    if not record:
        return {"completed_work": [], "next_week_plan": [], "weekly_reflection": []}
    return {
        "completed_work": _parse_bullets(record.get("completed_work")),
        "next_week_plan": _parse_bullets(record.get("next_week_plan")),
        "weekly_reflection": _parse_bullets(record.get("weekly_reflection")),
    }


def _parse_bullets(value: Any) -> list[str]:
    if isinstance(value, list):
        return _string_list(value)
    if not isinstance(value, str):
        return []
    items: list[str] = []
    for line in value.splitlines():
        text = line.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        if text:
            items.append(text)
    return _dedupe_texts(items)


def _sync_weekly_report_preferences_to_soul(
    db_path: str | Path,
    *,
    soul_path: Path,
) -> Path:
    connection = initialize_database(db_path)
    try:
        profile = repo.get_user_profile(connection)
        if profile is None:
            raise WeeklyReportMemoryUpdateError("user_profile_not_found")
        preferences = weekly_report_preferences_from_profile(profile)
    finally:
        connection.close()

    new_items: list[str] = []
    for key in WEEKLY_REPORT_MEMORY_KEYS:
        new_items.extend(preferences[key])
    text = soul_path.read_text(encoding="utf-8")
    start_marker, end_marker = _resolve_soul_markers(
        text,
        ("## 周报原则", "## 鍛ㄦ姤鍘熷垯"),
        ("## 输出纪律", "## 杈撳嚭绾緥"),
    )
    existing_items = _extract_section_bullets(text, start_marker, end_marker)
    merged_items = _merge_for_soul(existing_items, new_items)
    backup_path = _backup_soul(soul_path)
    rendered = _render_weekly_report_principles_section(merged_items)
    soul_path.write_text(
        _replace_section(text, start_marker, end_marker, rendered),
        encoding="utf-8",
    )
    return backup_path


def _resolve_soul_markers(
    text: str,
    start_markers: tuple[str, ...],
    end_markers: tuple[str, ...],
) -> tuple[str, str]:
    for start_marker in start_markers:
        start = text.find(start_marker)
        if start == -1:
            continue
        for end_marker in end_markers:
            end = text.find(end_marker, start + len(start_marker))
            if end > start:
                return start_marker, end_marker
    raise WeeklyReportMemoryUpdateError(
        f"SOUL.md section markers not found: {start_markers} -> {end_markers}"
    )


def _extract_section_bullets(text: str, start_marker: str, end_marker: str) -> list[str]:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start == -1 or end == -1 or end <= start:
        return []
    bullets: list[str] = []
    for line in text[start:end].splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return _dedupe_texts(bullets)


def _replace_section(text: str, start_marker: str, end_marker: str, rendered_section: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start == -1 or end == -1 or end <= start:
        raise WeeklyReportMemoryUpdateError(f"SOUL.md section markers not found: {start_marker} -> {end_marker}")
    return text[:start] + rendered_section.rstrip() + "\n\n" + text[end:]


def _render_weekly_report_principles_section(items: list[str]) -> str:
    lines = ["## 周报原则", ""]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines).rstrip()


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


def _backup_soul(soul_path: Path) -> Path:
    WEEKLY_REPORT_MEMORY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = WEEKLY_REPORT_MEMORY_BACKUP_DIR / f"SOUL_{stamp}.md"
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


def _clean_output_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("weekly_report_memory_items_must_be_lists")
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
    items: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _compact_text(value, 180)
        key = _normalize_key(text)
        if not key or key in seen:
            continue
        items.append(text)
        seen.add(key)
    return items


def _compact_text(value: Any, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def _normalize_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _contains_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def _strip_sentence_end(text: str) -> str:
    return str(text or "").strip().rstrip("。.!！?？")


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
