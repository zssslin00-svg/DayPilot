from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.workday_policy import is_workday, today_in_workday_timezone, workday_timezone  # noqa: E402


def test_monday_to_sunday_workday_policy() -> None:
    expected = {
        date(2026, 6, 8): True,
        date(2026, 6, 9): True,
        date(2026, 6, 10): True,
        date(2026, 6, 11): True,
        date(2026, 6, 12): True,
        date(2026, 6, 13): False,
        date(2026, 6, 14): False,
    }
    for day, should_be_workday in expected.items():
        assert is_workday(day) is should_be_workday


def test_2026_china_holiday_calendar_overrides_weekdays_and_weekends() -> None:
    expected = {
        date(2026, 1, 1): False,
        date(2026, 1, 4): True,
        date(2026, 2, 14): True,
        date(2026, 2, 16): False,
        date(2026, 5, 1): False,
        date(2026, 5, 9): True,
        date(2026, 9, 20): True,
        date(2026, 10, 10): True,
    }
    for day, should_be_workday in expected.items():
        assert is_workday(day) is should_be_workday


def test_missing_calendar_falls_back_to_monday_friday_policy() -> None:
    missing_path = ROOT / "does-not-exist" / "workday_calendar.json"
    assert is_workday(date(2026, 1, 1), calendar_path=missing_path) is True
    assert is_workday(date(2026, 1, 3), calendar_path=missing_path) is False
    assert workday_timezone(calendar_path=missing_path) == "Asia/Shanghai"
    assert isinstance(today_in_workday_timezone(calendar_path=missing_path), date)


def test_workday_policy_accepts_supported_date_inputs() -> None:
    assert is_workday("2026-06-08") is True
    assert is_workday(datetime(2026, 6, 13, 9, 30)) is False


def main() -> None:
    test_monday_to_sunday_workday_policy()
    test_2026_china_holiday_calendar_overrides_weekdays_and_weekends()
    test_missing_calendar_falls_back_to_monday_friday_policy()
    test_workday_policy_accepts_supported_date_inputs()
    print("PASS: workday policy marks Monday-Friday as workdays and weekends as non-workdays")


if __name__ == "__main__":
    main()
