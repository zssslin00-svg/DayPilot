from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.api.server import create_server  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import connect_database, initialize_database  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post_checkin(today: date, db_path: Path, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    request_body = {"completion_status": "completed", **body}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/checkin",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        try:
            with opener.open(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return int(response.status), payload
        except urllib.error.HTTPError as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _seed_goal(db_path: Path, goal_date: str) -> int:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily goal loop.",
            )
            daily_goal_id = repo.create_daily_goal(
                connection,
                goal_date=goal_date,
                context_snapshot={"source": "test"},
                generated_at=f"{goal_date} 09:00:00",
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=daily_goal_id,
                version_no=1,
                is_active=1,
                main_goal="Ship the check-in endpoint.",
                goal_reason="The daily loop needs a persisted evening check-in.",
                success_criteria=["Accept completion text", "Persist felt difficulty"],
                estimated_minutes=60,
                difficulty_level=2,
                minimum_version="The check-in endpoint saves one record.",
                goal_type="coding",
                revision_source="initial_generation",
            )
            return daily_goal_id
    finally:
        connection.close()


def test_checkin_allows_empty_text_fields_and_uses_completed_status_rate() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-08")

        status, payload = _post_checkin(
            date(2026, 6, 8),
            db_path,
            {
                "date": "2026-06-08",
                "goal_id": goal_id,
                "completion_text": "",
                "felt_difficulty": 3,
                "tomorrow_direction": "",
            },
        )

        assert status == 200
        assert payload["saved"] is True
        assert payload["updated"] is False
        assert payload["can_generate_weekly_report"] is False
        assert payload["checkin"]["tomorrow_direction"] is None
        assert payload["checkin"]["felt_difficulty"] == 3
        assert payload["checkin"]["parsed_completion_rate"] == 1.0
        assert payload["updated_difficulty"]["ability_state"]["target_difficulty_level"] == 2
        assert (
            payload["updated_difficulty"]["completion_parse_result"]["source"]
            == "completion_status_completed"
        )

        connection = connect_database(db_path)
        try:
            checkin = repo.get_daily_checkin_by_date(connection, "2026-06-08")
            assert checkin["completion_text"] == ""
            assert checkin["parsed_completion_rate"] == 1.0
            assert repo.get_daily_goal(connection, goal_id)["status"] == "checked_in"
        finally:
            connection.close()


def test_checkin_empty_completion_text_uses_incomplete_status_rate() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-incomplete-empty.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-08")

        status, payload = _post_checkin(
            date(2026, 6, 8),
            db_path,
            {
                "date": "2026-06-08",
                "goal_id": goal_id,
                "completion_status": "incomplete",
                "completion_text": "",
                "felt_difficulty": 3,
                "tomorrow_direction": "",
            },
        )

        assert status == 200
        assert payload["checkin"]["completion_text"] == ""
        assert payload["checkin"]["tomorrow_direction"] is None
        assert payload["checkin"]["parsed_completion_rate"] == 0.0
        assert (
            payload["updated_difficulty"]["completion_parse_result"]["source"]
            == "completion_status_incomplete"
        )


def test_checkin_updates_existing_submission() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-update.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-09")

        first_status, first_payload = _post_checkin(
            date(2026, 6, 9),
            db_path,
            {
                "date": "2026-06-09",
                "goal_id": goal_id,
                "completion_text": "完成了一半。",
                "felt_difficulty": 4,
            },
        )
        second_status, second_payload = _post_checkin(
            date(2026, 6, 9),
            db_path,
            {
                "date": "2026-06-09",
                "goal_id": goal_id,
                "completion_text": "补充后基本完成。",
                "felt_difficulty": 3,
                "tomorrow_direction": "明天继续前端表单",
            },
        )

        assert first_status == 200
        assert first_payload["updated"] is False
        assert second_status == 200
        assert second_payload["updated"] is True
        assert second_payload["checkin"]["completion_text"] == "补充后基本完成。"
        assert second_payload["checkin"]["parsed_completion_rate"] == 0.8

        connection = connect_database(db_path)
        try:
            checkin_count = connection.execute("SELECT COUNT(*) FROM daily_checkins").fetchone()[0]
            assert checkin_count == 1
        finally:
            connection.close()


def test_checkin_rejects_invalid_felt_difficulty() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-invalid.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-10")

        status, payload = _post_checkin(
            date(2026, 6, 10),
            db_path,
            {
                "date": "2026-06-10",
                "goal_id": goal_id,
                "completion_text": "有推进。",
                "felt_difficulty": 6,
            },
        )

        assert status == 400
        assert payload["error"] == "invalid_checkin"
        assert "felt_difficulty" in payload["detail"]


def test_china_holiday_checkin_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-holiday.sqlite3"
        goal_id = _seed_goal(db_path, "2026-01-01")

        status, payload = _post_checkin(
            date(2026, 1, 1),
            db_path,
            {
                "date": "2026-01-01",
                "goal_id": goal_id,
                "completion_text": "Holiday should not accept check-in.",
                "felt_difficulty": 3,
            },
        )

        assert status == 400
        assert payload["error"] == "invalid_checkin"


def test_china_makeup_weekend_checkin_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-makeup.sqlite3"
        goal_id = _seed_goal(db_path, "2026-01-04")

        status, payload = _post_checkin(
            date(2026, 1, 4),
            db_path,
            {
                "date": "2026-01-04",
                "goal_id": goal_id,
                "completion_text": "Completed the makeup workday goal.",
                "felt_difficulty": 3,
            },
        )

        assert status == 200
        assert payload["saved"] is True
        assert payload["checkin"]["checkin_date"] == "2026-01-04"


def test_friday_checkin_can_generate_weekly_report() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "daypilot-checkin-friday.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-12")

        status, payload = _post_checkin(
            date(2026, 6, 12),
            db_path,
            {
                "date": "2026-06-12",
                "goal_id": goal_id,
                "completion_text": "完成了周五目标。",
                "felt_difficulty": 2,
                "tomorrow_direction": "下周继续周报模块",
            },
        )

        assert status == 200
        assert payload["saved"] is True
        assert payload["can_generate_weekly_report"] is True
        assert payload["updated_difficulty"]["difficulty_update_event"]["new_difficulty"] >= 1


def main() -> None:
    test_checkin_allows_empty_text_fields_and_uses_completed_status_rate()
    test_checkin_empty_completion_text_uses_incomplete_status_rate()
    test_checkin_updates_existing_submission()
    test_checkin_rejects_invalid_felt_difficulty()
    test_china_holiday_checkin_is_rejected()
    test_china_makeup_weekend_checkin_is_allowed()
    test_friday_checkin_can_generate_weekly_report()
    print("PASS: POST /api/checkin saves, updates, validates, and reports Friday status")


if __name__ == "__main__":
    main()
