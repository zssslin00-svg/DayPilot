from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.services.today_goal_service import goal_output_from_record
from backend.services.weekly_report_service import report_output_from_record


@dataclass(frozen=True)
class HistoryResult:
    days: int
    start_date: str
    end_date: str
    daily_records: list[dict[str, Any]]
    weekly_reports: list[dict[str, Any]]


class HistoryValidationError(ValueError):
    """Raised when a history request is outside the supported local range."""


def get_history(
    db_path: str | Path,
    *,
    days: int,
    default_date: date,
) -> HistoryResult:
    if days < 1 or days > 180:
        raise HistoryValidationError("days must be between 1 and 180.")

    end = default_date
    start = end - timedelta(days=days - 1)
    connection = initialize_database(db_path)
    try:
        daily_records = [
            _attach_daily_payload(record)
            for record in repo.list_daily_goal_records_between(
                connection,
                start.isoformat(),
                end.isoformat(),
            )
        ]
        weekly_reports = [
            _attach_weekly_payload(connection, report)
            for report in repo.list_weekly_reports_between(
                connection,
                start.isoformat(),
                end.isoformat(),
            )
        ]
        return HistoryResult(
            days=days,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            daily_records=daily_records,
            weekly_reports=weekly_reports,
        )
    finally:
        connection.close()


def _attach_daily_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    payload["goal_output"] = goal_output_from_record(
        {
            "daily_goal": record.get("daily_goal"),
            "active_version": record.get("active_version"),
        }
    )
    return payload


def _attach_weekly_payload(connection, weekly_report: dict[str, Any]) -> dict[str, Any]:
    versions = repo.list_weekly_report_versions(connection, int(weekly_report["id"]))
    return {
        "weekly_report": weekly_report,
        "report_output": report_output_from_record(weekly_report),
        "versions": [
            {
                **version,
                "report_output": report_output_from_record(version),
            }
            for version in versions
        ],
        "weekly_focus": repo.list_weekly_focus_for_report(connection, int(weekly_report["id"])),
    }
