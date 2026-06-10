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
from backend.services.goal_generation_resources import validate_daily_goal_output  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_today_goal(today: date, db_path: Path) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/today-goal", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


def test_monday_goal_reads_marks_and_uses_weekly_focus() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-focus.sqlite3"
        seeded = _seed_previous_week_focus(db_path)

        status, payload = _get_today_goal(date(2026, 6, 15), db_path)

        assert status == 200
        assert payload["created"] is True
        goal = payload["goal"]
        goal_output = goal["goal_output"]
        validate_daily_goal_output(goal_output)
        assert goal_output["context_used"]["primary_driver"] == "last_week_focus"
        assert goal_output["context_used"]["tomorrow_direction_handling"] == "narrowed_to_daily_scope"
        assert "承接 weekly_focus" in goal_output["main_goal"]
        assert "weekly_focus 承接目标生成流程" in goal_output["main_goal"]

        snapshot = goal["daily_goal"]["context_snapshot"]
        assert snapshot["weekly_focus_ids"] == seeded["weekly_focus_ids"]
        assert snapshot["selected_weekly_focus_id"] == seeded["weekly_focus_ids"][0]
        assert "priority=5" in snapshot["focus_selection_reason"]
        assert snapshot["tomorrow_direction"] == "明天只有40分钟，想先写周报评估测试"
        assert snapshot["focus_deviation_log"]["decision"] == "bridge_to_weekly_focus"

        connection = connect_database(db_path)
        try:
            selected_focus = repo.get_weekly_focus(connection, seeded["weekly_focus_ids"][0])
            assert selected_focus["carried_into_goal_id"] == goal["daily_goal"]["id"]
            handoff = selected_focus["context_payload"]["handoff"]
            assert handoff["selected_on_date"] == "2026-06-15"
            assert handoff["status_after_selection"] == "in_progress"
            assert handoff["daily_goal_history"][0]["daily_goal_id"] == goal["daily_goal"]["id"]
        finally:
            connection.close()


def test_checkin_updates_selected_weekly_focus_progress() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-focus-checkin.sqlite3"
        seeded = _seed_previous_week_focus(db_path)
        _, goal_payload = _get_today_goal(date(2026, 6, 15), db_path)
        goal_id = goal_payload["goal"]["daily_goal"]["id"]

        status, payload = _post_checkin(
            date(2026, 6, 15),
            db_path,
            {
                "date": "2026-06-15",
                "goal_id": goal_id,
                "completion_text": "完成 weekly_focus 承接目标生成流程，留下测试记录。",
                "felt_difficulty": 3,
                "tomorrow_direction": "明天继续同一重点做回归验证",
            },
        )

        assert status == 200
        assert payload["checkin"]["parsed_completion_rate"] == 1.0

        connection = connect_database(db_path)
        try:
            selected_focus = repo.get_weekly_focus(connection, seeded["weekly_focus_ids"][0])
            handoff = selected_focus["context_payload"]["handoff"]
            assert handoff["progress_score"] == 1.0
            assert handoff["status_after_checkin"] == "completed"
            assert handoff["next_day_strategy"] == "select_next_focus_or_validate"
            assert handoff["progress_history"][0]["daily_goal_id"] == goal_id
        finally:
            connection.close()


def _seed_previous_week_focus(db_path: Path) -> dict[str, Any]:
    connection = initialize_database(db_path)
    weekly_focus_ids: list[int] = []
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily goal and weekly report loop.",
                current_focus_projects=["DayPilot MVP", "Weekly focus handoff"],
                default_available_minutes=80,
            )
            daypilot_project_id = repo.create_project(
                connection,
                name="DayPilot MVP",
                priority="P2",
                role="active",
                status="active",
                status_summary="",
                planning_bias="",
                source_payload={"source": "weekly-focus-test"},
            )
            repo.create_project(
                connection,
                name="Weekly focus handoff",
                priority="P2",
                role="active",
                status="active",
                status_summary="",
                planning_bias="",
                source_payload={"source": "weekly-focus-test"},
            )
            friday_goal_id = repo.create_daily_goal(
                connection,
                project_id=daypilot_project_id,
                goal_date="2026-06-12",
                context_snapshot={"source": "weekly-focus-test", "project_id": daypilot_project_id},
                generated_at="2026-06-12 09:00:00",
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=friday_goal_id,
                version_no=1,
                is_active=1,
                main_goal="完成周报到 weekly_focus 的提取记录",
                goal_reason="周五要为下周目标生成留下承接上下文。",
                success_criteria=["保存 weekly_focus", "记录承接来源"],
                estimated_minutes=80,
                difficulty_level=3,
                minimum_version="weekly_focus 记录可读取。",
                goal_type="coding",
                revision_source="initial_generation",
            )
            repo.create_daily_checkin(
                connection,
                daily_goal_id=friday_goal_id,
                checkin_date="2026-06-12",
                week_id="2026-W24",
                completion_text="完成 weekly_focus 提取候选。",
                felt_difficulty=3,
                tomorrow_direction="明天只有40分钟，想先写周报评估测试",
                parsed_completion_rate=1.0,
                completed_items=["weekly_focus 提取候选"],
                unfinished_items=[],
                blockers=[],
                actual_outputs=["backend/services/weekly_report_service.py"],
                processor_snapshot={"source": "weekly-focus-test"},
            )
            repo.create_ability_state(
                connection,
                state_date="2026-06-12",
                current_difficulty=3.0,
                target_difficulty_level=3,
                recent_completion_rate=0.8,
                recent_felt_difficulty_avg=3.0,
                default_estimated_minutes=80,
                preferred_goal_type_weights={"coding": 0.6, "testing": 0.4},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["目标太大"],
                adjustment_direction="hold",
                update_reason="Seed weekly focus handoff state.",
                is_current=1,
            )
            weekly_report_id = repo.create_weekly_report(
                connection,
                week_id="2026-W24",
                week_start_date="2026-06-08",
                week_end_date="2026-06-12",
                generated_on_date="2026-06-12",
                completed_work="- 完成 weekly_focus 提取候选。",
                next_week_plan="- 跑通 weekly_focus 承接目标生成流程。",
                weekly_reflection="- 下周要让周报真正影响每日目标。",
                report_text="本周完成工作\n- 完成 weekly_focus 提取候选。",
                source_snapshot={"daily_goal_ids": [friday_goal_id]},
            )
            for order, (focus_text, focus_type, priority) in enumerate(
                [
                    ("weekly_focus 承接目标生成流程", "testing", 5),
                    ("前端周报展示细节可复查", "design", 4),
                ],
                start=1,
            ):
                weekly_focus_ids.append(
                    repo.create_weekly_focus(
                        connection,
                        weekly_report_id=weekly_report_id,
                        source_week_id="2026-W24",
                        target_week_id="2026-W25",
                        focus_order=order,
                        focus_text=focus_text,
                        desired_outcome=f"{focus_text}形成可验证结果。",
                        focus_type=focus_type,
                        priority=priority,
                        status="active",
                        context_payload={
                            "source": ["weekly_report.next_week_plan"],
                            "success_criteria": [f"{focus_text}有测试证据"],
                        },
                    )
                )
    finally:
        connection.close()
    return {"weekly_focus_ids": weekly_focus_ids}


def main() -> None:
    test_monday_goal_reads_marks_and_uses_weekly_focus()
    test_checkin_updates_selected_weekly_focus_progress()
    print("PASS: weekly_focus is handed off to Monday goals and updated by check-in")


if __name__ == "__main__":
    main()
