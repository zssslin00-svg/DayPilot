from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALENDAR_PATH = PROJECT_ROOT / "data" / "config" / "workday_calendar.json"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def is_workday(
    day: date | datetime | str,
    *,
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
) -> bool:
    """Return True for configured workdays, falling back to Monday-Friday."""

    value = _coerce_date(day)
    calendar = _load_calendar(Path(calendar_path))
    year = str(value.year)
    year_config = calendar.get("years", {}).get(year, {})
    date_text = value.isoformat()
    if date_text in _string_set(year_config.get("makeup_workdays")):
        return True
    if date_text in _string_set(year_config.get("holiday_dates")):
        return False
    return value.isoweekday() <= 5


def workday_timezone(
    *,
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
) -> str:
    calendar = _load_calendar(Path(calendar_path))
    timezone = str(calendar.get("timezone") or "").strip()
    return timezone or DEFAULT_TIMEZONE


def today_in_workday_timezone(
    *,
    calendar_path: str | Path = DEFAULT_CALENDAR_PATH,
) -> date:
    timezone = workday_timezone(calendar_path=calendar_path)
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _coerce_date(day: date | datetime | str) -> date:
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    if isinstance(day, str):
        return date.fromisoformat(day)
    raise TypeError("day must be a date, datetime, or YYYY-MM-DD string")


@lru_cache(maxsize=8)
def _load_calendar(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"timezone": DEFAULT_TIMEZONE, "years": {}}
    if not isinstance(payload, dict):
        return {"timezone": DEFAULT_TIMEZONE, "years": {}}
    years = payload.get("years")
    if not isinstance(years, dict):
        payload["years"] = {}
    return payload


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
