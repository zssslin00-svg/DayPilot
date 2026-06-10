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


WEEK_ID = "2026-W24"
FRIDAY = date(2026, 6, 12)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    db_path: Path,
    method: str,
    path: str,
    *,
    today: date = FRIDAY,
    body: dict[str, Any] | None = None,
    soul_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
        soul_path=soul_path or Path("SOUL.md"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    request_body = body
    if path == "/api/checkin" and request_body is not None:
        request_body = {"completion_status": "completed", **request_body}
    data = None if request_body is None else json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if request_body is not None else {},
        method=method,
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


def _soul_file(root: Path) -> Path:
    soul_path = root / "SOUL.md"
    soul_path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付。",
                "",
                "## 避免事项",
                "",
                "- 不要太抽象。",
                "",
                "## 时间预算与目标数量",
                "- 用户每天有效工作时间约为 4 小时。",
                "",
                "## 每日目标原则",
                "",
                "- 目标要可检查。",
                "",
                "## 周报原则",
                "",
                "- 周报只能总结有证据的完成结果。",
                "",
                "## 输出纪律",
                "",
                "- 只输出需要的内容。",
            ]
        ),
        encoding="utf-8",
    )
    return soul_path


def test_history_reads_existing_records_without_generating_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "history.sqlite3"
        seeded = _seed_workweek(db_path)

        connection = connect_database(db_path)
        try:
            before_count = connection.execute("SELECT COUNT(*) FROM daily_goals").fetchone()[0]
        finally:
            connection.close()

        status, payload = _request(db_path, "GET", "/api/history?days=7")

        assert status == 200
        assert payload["start_date"] == "2026-06-06"
        assert payload["end_date"] == "2026-06-12"
        assert len(payload["daily_records"]) == 5
        assert payload["daily_records"][0]["daily_goal"]["goal_date"] == "2026-06-12"
        assert payload["daily_records"][0]["goal_output"]["schema_version"] == "daily_goal.v1"
        assert payload["daily_records"][0]["daily_checkin"] is not None
        assert payload["weekly_reports"] == []

        connection = connect_database(db_path)
        try:
            after_count = connection.execute("SELECT COUNT(*) FROM daily_goals").fetchone()[0]
            assert after_count == before_count
            assert seeded["goal_ids"]
        finally:
            connection.close()


def test_weekly_report_feedback_creates_version_chain() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-feedback.sqlite3"
        soul_path = _soul_file(Path(temp_dir))
        _seed_workweek(db_path)
        status, payload = _request(db_path, "POST", "/api/weekly-report/generate", body={"week_id": WEEK_ID})
        assert status == 200
        assert len(payload["weekly_report_versions"]) == 1

        status, revised = _request(
            db_path,
            "POST",
            "/api/weekly-report/feedback",
            body={"week_id": WEEK_ID, "message": "把下周计划写得更可验收。"},
            soul_path=soul_path,
        )

        assert status == 200
        assert revised["created"] is False
        assert len(revised["weekly_report_versions"]) == 2
        assert revised["weekly_report_versions"][-1]["revision_source"] == "user_feedback"
        memory = revised["weekly_report_memory_update"]
        assert memory["status"] in {"applied", "queued"}
        assert memory["applied_items_count"] >= 1

        connection = connect_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            weekly_preferences = profile["goal_preferences"]["weekly_report_preferences"]
            assert any(weekly_preferences[key] for key in weekly_preferences)
        finally:
            connection.close()
        assert "下周计划要写成可验收的结果目标" in soul_path.read_text(encoding="utf-8")
        assert revised["weekly_report_versions"][-1]["feedback_message"] == "把下周计划写得更可验收。"

        bad_status, bad_payload = _request(
            db_path,
            "POST",
            "/api/weekly-report/feedback",
            body={"week_id": WEEK_ID, "message": ""},
        )
        assert bad_status == 400
        assert bad_payload["error"] == "invalid_weekly_report_feedback"


def test_editing_checkin_refreshes_existing_weekly_report() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "checkin-refresh.sqlite3"
        seeded = _seed_workweek(db_path)
        status, payload = _request(db_path, "POST", "/api/weekly-report/generate", body={"week_id": WEEK_ID})
        assert status == 200
        assert len(payload["weekly_report_versions"]) == 1

        goal_id = seeded["goal_by_date"]["2026-06-10"]
        status, updated = _request(
            db_path,
            "POST",
            "/api/checkin",
            body={
                "date": "2026-06-10",
                "goal_id": goal_id,
                "completion_text": "补充修正后，完成了规则评估记录和问题清单。",
                "felt_difficulty": 3,
                "tomorrow_direction": "继续收敛周报重点",
            },
        )

        assert status == 200
        assert updated["updated"] is True
        assert updated["weekly_report_refresh"]["status"] == "refreshed"

        connection = connect_database(db_path)
        try:
            weekly_report = repo.get_weekly_report_by_week(connection, WEEK_ID)
            assert weekly_report is not None
            versions = repo.list_weekly_report_versions(connection, int(weekly_report["id"]))
            assert len(versions) == 2
            assert versions[-1]["revision_source"] == "checkin_refresh"
            checkin = repo.get_daily_checkin_by_date(connection, "2026-06-10")
            assert "补充修正后" in checkin["completion_text"]
        finally:
            connection.close()


def _seed_workweek(db_path: Path) -> dict[str, Any]:
    connection = initialize_database(db_path)
    goal_ids: list[int] = []
    goal_by_date: dict[str, int] = {}
    rows = [
        ("2026-06-08", "今日目标接口", "继续历史页"),
        ("2026-06-09", "check-in 保存", "继续周报聚合"),
        ("2026-06-10", "规则评估记录", "补齐周报反馈"),
        ("2026-06-11", "周报质量检查", "收敛范围"),
        ("2026-06-12", "周报生成接口", "下周承接 weekly_focus"),
    ]
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a reliable DayPilot daily loop.",
                current_focus_projects=["DayPilot"],
                default_available_minutes=90,
            )
            for goal_date, item, direction in rows:
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date=goal_date,
                    context_snapshot={"source": "history-weekly-test"},
                    generated_at=f"{goal_date} 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal=f"完成 DayPilot {item} 的可验收切片",
                    goal_reason=f"{item} 支撑日用补强。",
                    success_criteria=[f"交付 {item}", "记录验收结果"],
                    estimated_minutes=70,
                    difficulty_level=3,
                    minimum_version=f"{item} 有可检查记录。",
                    stretch_challenge="补一条回归测试。",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date=goal_date,
                    week_id=WEEK_ID,
                    completion_text=f"完成 {item}，留下可复查记录。",
                    felt_difficulty=3,
                    tomorrow_direction=direction,
                    parsed_completion_rate=0.9,
                    completed_items=[item],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=[f"artifact/{item}"],
                    processor_snapshot={"source": "history-weekly-test"},
                )
                goal_ids.append(daily_goal_id)
                goal_by_date[goal_date] = daily_goal_id
            repo.create_ability_state(
                connection,
                state_date="2026-06-12",
                current_difficulty=3.0,
                target_difficulty_level=3,
                recent_completion_rate=0.9,
                recent_felt_difficulty_avg=3.0,
                default_estimated_minutes=90,
                preferred_goal_type_weights={"coding": 0.7, "testing": 0.3},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["目标太大"],
                adjustment_direction="hold",
                update_reason="Seeded state.",
                is_current=1,
            )
    finally:
        connection.close()
    return {"goal_ids": goal_ids, "goal_by_date": goal_by_date}


def main() -> None:
    test_history_reads_existing_records_without_generating_goal()
    test_weekly_report_feedback_creates_version_chain()
    test_editing_checkin_refreshes_existing_weekly_report()
    print("PASS: history, weekly report feedback, and check-in refresh APIs verified")


if __name__ == "__main__":
    main()
