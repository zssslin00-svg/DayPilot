from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.schemas.json_schema import validate_json_schema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEEKLY_REPORT_SCHEMA_PATH = PROJECT_ROOT / "backend" / "schemas" / "weekly_report.schema.json"


def load_weekly_report_schema() -> dict[str, Any]:
    return json.loads(WEEKLY_REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_weekly_report_output(report: dict[str, Any]) -> None:
    validate_json_schema(report, load_weekly_report_schema())
