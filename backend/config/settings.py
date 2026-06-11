from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from backend.config.runtime_paths import default_env_path, default_llm_log_dir

DEFAULT_ENV_PATH = default_env_path()


@dataclass(frozen=True)
class DayPilotSettings:
    llm_mode: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: int
    deepseek_max_tokens: int
    deepseek_thinking: str
    llm_log_enabled: bool = True
    llm_log_dir: str = str(default_llm_log_dir())

    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key)


def load_daypilot_settings(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> DayPilotSettings:
    """Load settings from `.env`, then let real environment variables override them."""

    source_env = dict(os.environ if env is None else env)
    file_env = _read_dotenv(Path(dotenv_path) if dotenv_path is not None else DEFAULT_ENV_PATH)
    merged = {**file_env, **source_env}

    llm_mode = _choice(
        merged.get("DAYPILOT_LLM_MODE", "auto"),
        allowed={"mock", "deepseek", "auto"},
        default="auto",
    )
    thinking = _choice(
        merged.get("DEEPSEEK_THINKING", "disabled"),
        allowed={"enabled", "disabled"},
        default="disabled",
    )

    return DayPilotSettings(
        llm_mode=llm_mode,
        deepseek_api_key=_blank_to_none(merged.get("DEEPSEEK_API_KEY")),
        deepseek_base_url=(merged.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
        deepseek_model=merged.get("DEEPSEEK_MODEL") or "deepseek-v4-pro",
        deepseek_timeout_seconds=_positive_int(merged.get("DEEPSEEK_TIMEOUT_SECONDS"), 30),
        deepseek_max_tokens=_positive_int(merged.get("DEEPSEEK_MAX_TOKENS"), 1600),
        deepseek_thinking=thinking,
        llm_log_enabled=_bool(merged.get("DAYPILOT_LLM_LOG_ENABLED"), default=True),
        llm_log_dir=merged.get("DAYPILOT_LLM_LOG_DIR") or str(default_llm_log_dir()),
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _choice(value: str, *, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def _bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
