from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config.settings import DayPilotSettings


LOG_STREAMS = {"deepseek", "mock", "errors"}


def write_llm_log(
    settings: DayPilotSettings,
    stream: str,
    *,
    event_id: str | None = None,
    task_name: str,
    prompt_version: str | None,
    llm_mode_requested: str | None,
    llm_mode_used: str,
    provider: str,
    model_name: str | None,
    messages: list[dict[str, str]] | None,
    raw_output: Any = None,
    validated_output: Any = None,
    validator_status: str | None = None,
    error: str | None = None,
    fallback_reason: str | None = None,
    soul_loaded: bool | None = None,
    soul_path: str | None = None,
    usage: Any = None,
    response_id: str | None = None,
    attempt: str | None = None,
    repair_of_event_id: str | None = None,
    repair_reason: str | None = None,
) -> Path | None:
    """Append one local JSONL LLM log record.

    Logging is intentionally best-effort. A logging failure must not block the
    user's daily workflow or change fallback behavior.
    """

    if not settings.llm_log_enabled:
        return None
    if stream not in LOG_STREAMS:
        stream = "errors"

    try:
        now = datetime.now()
        event = {
            "event_id": event_id or str(uuid.uuid4()),
            "created_at": now.isoformat(timespec="seconds"),
            "task_name": task_name,
            "prompt_version": prompt_version,
            "llm_mode_requested": llm_mode_requested,
            "llm_mode_used": llm_mode_used,
            "provider": provider,
            "model_name": model_name,
            "messages": messages or [],
            "raw_output": raw_output,
            "validated_output": validated_output,
            "validator_status": validator_status,
            "error": error,
            "fallback_reason": fallback_reason,
            "soul_loaded": soul_loaded,
            "soul_path": soul_path,
            "usage": usage,
            "response_id": response_id,
            "attempt": attempt,
            "repair_of_event_id": repair_of_event_id,
            "repair_reason": repair_reason,
        }
        path = Path(settings.llm_log_dir) / stream / f"{now.date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":")))
            handle.write("\n")
        return path
    except Exception:
        return None
