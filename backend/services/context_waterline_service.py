from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.config.settings import DayPilotSettings, load_daypilot_settings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.schemas.json_schema import validate_json_schema
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_SCHEMA_PATH = PROJECT_ROOT / "backend" / "schemas" / "career_chat_memory_summary.schema.json"

SUMMARY_PROMPT_VERSION_MOCK = "career_chat_memory_summary_v1_mock"
SUMMARY_PROMPT_VERSION_DEEPSEEK = "career_chat_memory_summary_v1_deepseek"
SUMMARY_MOCK_MODEL_NAME = "mock-career-chat-memory-summary"

TIER_1_RATIO = 0.60
TIER_2_RATIO = 0.80
TIER_3_RATIO = 0.95


@dataclass(frozen=True)
class ContextWaterlineResult:
    context: dict[str, Any]
    soul_for_prompt: str
    metadata: dict[str, Any]


def estimate_prompt_tokens(value: Any) -> int:
    """Approximate prompt tokens without provider-specific tokenizers."""

    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    cjk_count = 0
    other_count = 0
    for char in value:
        if "\u4e00" <= char <= "\u9fff":
            cjk_count += 1
        elif not char.isspace():
            other_count += 1
    return max(1, int(math.ceil(cjk_count + other_count / 4)))


def prepare_career_chat_context(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    context: dict[str, Any],
    soul_text: str,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
) -> ContextWaterlineResult:
    resolved_settings = settings or load_daypilot_settings()
    token_limit = max(1, int(getattr(resolved_settings, "context_limit_tokens", 64_000)))
    working = copy.deepcopy(context)
    soul_for_prompt = str(soul_text or "")
    metadata = _base_metadata(working, soul_for_prompt, token_limit)

    initial_ratio = float(metadata["initial_ratio"])
    if initial_ratio < TIER_1_RATIO:
        metadata.update(_final_metadata("tier_0", working, soul_for_prompt, token_limit))
        working["context_waterline"] = metadata
        return ContextWaterlineResult(working, soul_for_prompt, metadata)

    tier1_stats: dict[str, int] = {}
    soul_for_prompt = _apply_tier1_snip(working, soul_for_prompt, tier1_stats)
    metadata["tier1"] = tier1_stats
    if initial_ratio < TIER_2_RATIO:
        metadata.update(_final_metadata("tier_1", working, soul_for_prompt, token_limit))
        working["context_waterline"] = metadata
        return ContextWaterlineResult(working, soul_for_prompt, metadata)

    tier2_stats: dict[str, int] = {}
    _apply_tier2_prune(working, tier2_stats, keep_messages=6)
    metadata["tier2"] = tier2_stats
    if initial_ratio < TIER_3_RATIO:
        metadata.update(_final_metadata("tier_2", working, soul_for_prompt, token_limit))
        working["context_waterline"] = metadata
        return ContextWaterlineResult(working, soul_for_prompt, metadata)

    post_prune_ratio = _ratio(working, soul_for_prompt, token_limit)
    metadata["post_prune_ratio"] = post_prune_ratio
    if post_prune_ratio < TIER_3_RATIO:
        metadata.update(_final_metadata("tier_2", working, soul_for_prompt, token_limit))
        working["context_waterline"] = metadata
        return ContextWaterlineResult(working, soul_for_prompt, metadata)

    tier3_stats = _apply_tier3_summary(
        db_path,
        original_context=context,
        working_context=working,
        settings=resolved_settings,
        soul_path=soul_path,
    )
    metadata["tier3"] = tier3_stats
    metadata.update(_final_metadata("tier_3", working, soul_for_prompt, token_limit))
    working["context_waterline"] = metadata
    return ContextWaterlineResult(working, soul_for_prompt, metadata)


def _base_metadata(context: dict[str, Any], soul_text: str, token_limit: int) -> dict[str, Any]:
    tokens = _estimate_context_tokens(context, soul_text)
    return {
        "strategy": "career_chat_waterline_v1",
        "context_limit_tokens": token_limit,
        "initial_estimated_tokens": tokens,
        "initial_ratio": round(tokens / token_limit, 4),
        "thresholds": {
            "tier_1": TIER_1_RATIO,
            "tier_2": TIER_2_RATIO,
            "tier_3": TIER_3_RATIO,
        },
    }


def _final_metadata(tier: str, context: dict[str, Any], soul_text: str, token_limit: int) -> dict[str, Any]:
    tokens = _estimate_context_tokens(context, soul_text)
    return {
        "tier": tier,
        "final_estimated_tokens": tokens,
        "final_ratio": round(tokens / token_limit, 4),
    }


def _estimate_context_tokens(context: dict[str, Any], soul_text: str) -> int:
    payload = {
        "latest_message": context.get("latest_message"),
        "available_minutes": context.get("available_minutes"),
        "today": context.get("today"),
        "career_profile": (context.get("user_profile") or {}).get("career_profile") or {},
        "long_term_direction": (context.get("user_profile") or {}).get("long_term_direction") or "",
        "goal_preferences": (context.get("user_profile") or {}).get("goal_preferences") or {},
        "avoid_patterns": (context.get("user_profile") or {}).get("avoid_patterns") or [],
        "active_projects": context.get("active_projects") or [],
        "completed_projects": context.get("completed_projects") or [],
        "ability_state": context.get("ability_state") or {},
        "recent_daily_goals": context.get("recent_daily_goals") or [],
        "recent_checkins": context.get("recent_checkins") or [],
        "recent_feedback_messages": context.get("recent_feedback_messages") or [],
        "recent_weekly_focus": context.get("recent_weekly_focus") or [],
        "chat_history": context.get("chat_history") or [],
        "conversation_summary": context.get("conversation_summary") or {},
        "omitted_counts": context.get("omitted_counts") or {},
        "soul": soul_text,
    }
    return estimate_prompt_tokens(payload)


def _ratio(context: dict[str, Any], soul_text: str, token_limit: int) -> float:
    return _estimate_context_tokens(context, soul_text) / token_limit


def _apply_tier1_snip(
    context: dict[str, Any],
    soul_text: str,
    stats: dict[str, int],
) -> str:
    if len(soul_text) > 12_000:
        omitted = len(soul_text) - 12_000
        soul_text = soul_text[:12_000].rstrip() + f"\n\n[snipped {omitted} chars from SOUL.md]"
        stats["snipped_soul_chars"] = omitted

    chat_history = []
    for message in context.get("chat_history") or []:
        chat_history.append(_tier1_message(message, stats))
    context["chat_history"] = chat_history

    for key in ("recent_daily_goals", "recent_checkins", "recent_feedback_messages", "recent_weekly_focus"):
        context[key] = [_trim_long_strings(item, 400, stats) for item in context.get(key) or []]
    return soul_text


def _tier1_message(message: Mapping[str, Any], stats: dict[str, int]) -> dict[str, Any]:
    item = dict(message)
    if str(item.get("role") or "") == "assistant":
        content = str(item.get("content") or "")
        if len(content) > 600:
            item["content"] = _first_paragraphs(content, max_paragraphs=2, max_chars=600)
            item["content"] += f"\n[snipped {len(content) - len(item['content'])} chars]"
            stats["snipped_assistant_messages"] = stats.get("snipped_assistant_messages", 0) + 1
    recommendations = item.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        compacted = []
        for recommendation in recommendations:
            if not isinstance(recommendation, Mapping):
                continue
            compacted.append(
                {
                    key: recommendation[key]
                    for key in ("title", "deliverable", "project_binding")
                    if key in recommendation
                }
            )
        if compacted != recommendations:
            stats["snipped_recommendations"] = stats.get("snipped_recommendations", 0) + 1
        item["recommendations"] = compacted
    return item


def _apply_tier2_prune(context: dict[str, Any], stats: dict[str, int], *, keep_messages: int) -> None:
    history = list(context.get("chat_history") or [])
    if len(history) > keep_messages:
        omitted = len(history) - keep_messages
        context["chat_history"] = [
            {
                "role": "assistant",
                "content": f"[Content compacted to save space: {omitted} earlier chat messages omitted.]",
                "recommendations": [],
            },
            *history[-keep_messages:],
        ]
        stats["pruned_chat_messages"] = omitted

    for message in context.get("chat_history") or []:
        if str(message.get("role") or "") == "assistant":
            content = str(message.get("content") or "")
            if len(content) > 220 and "[Content compacted" not in content:
                message["content"] = _first_sentences(content, max_sentences=2, max_chars=220) + "\n[truncated]"
                stats["truncated_assistant_messages"] = stats.get("truncated_assistant_messages", 0) + 1

    omitted_counts = dict(context.get("omitted_counts") or {})
    for key in ("recent_daily_goals", "recent_checkins", "recent_feedback_messages", "recent_weekly_focus"):
        items = list(context.get(key) or [])
        if len(items) > 3:
            omitted_counts[key] = len(items) - 3
            context[key] = items[:3]
    if omitted_counts:
        context["omitted_counts"] = omitted_counts


def _apply_tier3_summary(
    db_path: str | Path,
    *,
    original_context: dict[str, Any],
    working_context: dict[str, Any],
    settings: DayPilotSettings,
    soul_path: str | Path,
) -> dict[str, Any]:
    session_id = int(original_context["session_id"])
    all_history = list(original_context.get("chat_history") or [])
    recent_messages = all_history[-4:]
    recent_ids = {int(item["id"]) for item in recent_messages if item.get("id") is not None}

    existing = _load_existing_summary(db_path, session_id)
    covered_through = int(existing.get("covered_through_message_id") or 0) if existing else 0
    delta_messages = [
        item
        for item in all_history
        if item.get("id") is not None
        and int(item["id"]) > covered_through
        and int(item["id"]) not in recent_ids
    ]
    summary_payload = dict(existing.get("summary_payload") or {}) if existing else _empty_summary()
    generated = False
    llm_metadata = dict(existing.get("llm_metadata") or {}) if existing else {}
    source_message_ids = list(existing.get("source_message_ids") or []) if existing else []

    if delta_messages:
        result = _generate_or_mock_summary(
            previous_summary=summary_payload,
            delta_messages=delta_messages,
            settings=settings,
            soul_path=soul_path,
        )
        summary_payload = result["summary_payload"]
        llm_metadata = result["llm_metadata"]
        delta_ids = [int(item["id"]) for item in delta_messages if item.get("id") is not None]
        source_message_ids = _dedupe_ints([*source_message_ids, *delta_ids])[-200:]
        covered_through = max(delta_ids)
        generated = True
        connection = initialize_database(db_path)
        try:
            with connection:
                existing = repo.upsert_career_chat_memory_summary(
                    connection,
                    session_id=session_id,
                    summary_payload=summary_payload,
                    covered_through_message_id=covered_through,
                    source_message_ids=source_message_ids,
                    llm_metadata=llm_metadata,
                )
        finally:
            connection.close()

    if summary_payload and summary_payload != _empty_summary():
        working_context["conversation_summary"] = summary_payload
    working_context["chat_history"] = [_tier1_message(item, {}) for item in recent_messages]
    return {
        "summary_generated": generated,
        "summary_id": existing.get("id") if existing else None,
        "covered_through_message_id": covered_through or None,
        "source_message_count": len(source_message_ids),
        "delta_message_count": len(delta_messages),
        "retained_recent_message_count": len(recent_messages),
    }


def _load_existing_summary(db_path: str | Path, session_id: int) -> dict[str, Any] | None:
    connection = initialize_database(db_path)
    try:
        return repo.get_career_chat_memory_summary_by_session(connection, session_id)
    finally:
        connection.close()


def _generate_or_mock_summary(
    *,
    previous_summary: dict[str, Any],
    delta_messages: list[dict[str, Any]],
    settings: DayPilotSettings,
    soul_path: str | Path,
) -> dict[str, Any]:
    context = {
        "previous_summary": _normalize_summary(previous_summary),
        "delta_messages": _summary_delta_messages(delta_messages),
    }
    llm_result = generate_json_with_fallback(
        task_name="career_chat_memory_summary",
        prompt_version_deepseek=SUMMARY_PROMPT_VERSION_DEEPSEEK,
        prompt_version_mock=SUMMARY_PROMPT_VERSION_MOCK,
        mock_model_name=SUMMARY_MOCK_MODEL_NAME,
        build_messages=lambda _soul: _summary_messages(context),
        mock_generate=lambda: MockCareerChatMemorySummaryAdapter().generate(context),
        validator=validate_career_chat_memory_summary,
        normalizer=normalize_career_chat_memory_summary,
        settings=settings,
        soul_path=soul_path,
    )
    return {
        "summary_payload": normalize_career_chat_memory_summary(llm_result.output),
        "llm_metadata": llm_result.metadata,
    }


class MockCareerChatMemorySummaryAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        previous = _normalize_summary(context.get("previous_summary"))
        delta_messages = context.get("delta_messages") if isinstance(context.get("delta_messages"), list) else []
        progress = list(previous["progress"])
        files = list(previous["files"])
        todo = list(previous["todo"])
        context_items = list(previous["context"])

        for message in delta_messages:
            text = str(message.get("content") or "")
            role = str(message.get("role") or "")
            compact = _compact_text(text, 160)
            if not compact:
                continue
            if role == "user":
                context_items.append(f"用户提到：{compact}")
            else:
                progress.append(f"助手建议：{compact}")
            files.extend(_extract_file_hints(text))
            if any(token in text.lower() for token in ("todo", "待办", "下一步", "first_step", "先")):
                todo.append(compact)

        return {
            "schema_version": "career_chat_memory_summary.v1",
            "progress": _dedupe_texts(progress, max_items=12, max_chars=180),
            "files": _dedupe_texts(files, max_items=12, max_chars=180),
            "todo": _dedupe_texts(todo, max_items=12, max_chars=180),
            "context": _dedupe_texts(context_items, max_items=16, max_chars=220),
            "source_message_count": int(previous.get("source_message_count") or 0) + len(delta_messages),
        }


def _summary_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "task": "Merge previous career chat memory summary with new delta messages.",
        "required_schema": "career_chat_memory_summary.v1",
        "rules": [
            "Do not invent facts.",
            "Keep only project names, decisions, user preferences, mistakes, constraints, files, and TODOs.",
            "Use concise Chinese.",
            "Return exactly one JSON object without Markdown fences.",
        ],
        "previous_summary": context["previous_summary"],
        "delta_messages": context["delta_messages"],
    }
    return [
        {
            "role": "system",
            "content": (
                "You summarize DayPilot career chat history for future context compression. "
                "Return only valid JSON matching career_chat_memory_summary.v1."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def validate_career_chat_memory_summary(output: dict[str, Any]) -> None:
    validate_json_schema(output, _load_summary_schema())


def normalize_career_chat_memory_summary(output: Any) -> dict[str, Any]:
    if not isinstance(output, Mapping):
        raise ValueError("career_chat_memory_summary_not_object")
    return {
        "schema_version": "career_chat_memory_summary.v1",
        "progress": _string_items(output.get("progress"), max_items=12, max_chars=180),
        "files": _string_items(output.get("files"), max_items=12, max_chars=180),
        "todo": _string_items(output.get("todo"), max_items=12, max_chars=180),
        "context": _string_items(output.get("context"), max_items=16, max_chars=220),
        "source_message_count": _non_negative_int(output.get("source_message_count")),
    }


def _load_summary_schema() -> dict[str, Any]:
    return json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))


def _summary_delta_messages(delta_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for item in delta_messages:
        compacted.append(
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "content": _compact_text(item.get("content"), 1000),
                "recommendations": [
                    {
                        "title": recommendation.get("title"),
                        "deliverable": recommendation.get("deliverable"),
                        "project_binding": recommendation.get("project_binding"),
                    }
                    for recommendation in item.get("recommendations") or []
                    if isinstance(recommendation, Mapping)
                ],
            }
        )
    return compacted


def _empty_summary() -> dict[str, Any]:
    return {
        "schema_version": "career_chat_memory_summary.v1",
        "progress": [],
        "files": [],
        "todo": [],
        "context": [],
        "source_message_count": 0,
    }


def _normalize_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return _empty_summary()
    return normalize_career_chat_memory_summary(value)


def _trim_long_strings(value: Any, max_chars: int, stats: dict[str, int]) -> Any:
    if isinstance(value, Mapping):
        return {key: _trim_long_strings(item, max_chars, stats) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim_long_strings(item, max_chars, stats) for item in value]
    if isinstance(value, str) and len(value) > max_chars:
        stats["snipped_recent_fields"] = stats.get("snipped_recent_fields", 0) + 1
        return value[:max_chars].rstrip() + f" [snipped {len(value) - max_chars} chars]"
    return value


def _first_paragraphs(text: str, *, max_paragraphs: int, max_chars: int) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", str(text or "")) if part.strip()]
    compact = "\n\n".join(paragraphs[:max_paragraphs]) if paragraphs else str(text or "")
    return _trim_at_boundary(compact, max_chars)


def _first_sentences(text: str, *, max_sentences: int, max_chars: int) -> str:
    parts = [part for part in re.split(r"(?<=[。！？.!?])\s*", str(text or "")) if part.strip()]
    compact = "".join(parts[:max_sentences]).strip() if parts else str(text or "").strip()
    return _trim_at_boundary(compact, max_chars)


def _trim_at_boundary(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rstrip()
    for separator in ("\n\n", "\n", "。", "！", "？", ".", "；", ";", "，", ","):
        index = truncated.rfind(separator)
        if index >= max(12, int(max_chars * 0.55)):
            end = index if separator.startswith("\n") else index + len(separator)
            return truncated[:end].rstrip()
    return truncated


def _compact_text(value: Any, max_chars: int) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split()).strip()[:max_chars]


def _string_items(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    return _dedupe_texts(raw_items, max_items=max_items, max_chars=max_chars)


def _dedupe_texts(values: list[Any], *, max_items: int, max_chars: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact_text(value, max_chars)
        key = re.sub(r"\s+", "", text.lower())
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= max_items:
            break
    return result


def _dedupe_ints(values: list[Any]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number not in seen:
            result.append(number)
            seen.add(number)
    return result


def _extract_file_hints(text: str) -> list[str]:
    pattern = r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|md|json|sql|docx|html|css)"
    return _dedupe_texts(re.findall(pattern, text), max_items=12, max_chars=180)


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)
