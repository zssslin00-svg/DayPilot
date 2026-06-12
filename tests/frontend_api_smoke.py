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
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.api.server import create_server  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402


FRIDAY = date(2026, 6, 12)
WEEK_ID = "2026-W24"


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_static_frontend() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    port = _free_port()
    handler = partial(QuietStaticHandler, directory=str(ROOT / "frontend"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


def _start_backend(db_path: Path, soul_path: Path) -> tuple[Any, threading.Thread, str]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: FRIDAY,
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


def _get_json(url: str) -> tuple[int, dict[str, Any]]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return int(response.status), payload


def _post_json(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request_body = body
    if "/api/checkin" in url:
        request_body = {"completion_status": "completed", **body}
    request = urllib.request.Request(
        url,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    except urllib.error.HTTPError as response:
        payload = json.loads(response.read().decode("utf-8"))
        return int(response.status), payload


def _read_text(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _stop_server(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    _write_soul_current_projects(
        path,
        "1. DayPilot smoke acceptance：当前进度：准备 smoke 验收。项目今日目标：完成 DayPilot smoke 验收。",
    )
    return path


def _write_soul_current_projects(path: Path, current_projects: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                current_projects.strip(),
                "",
                "每日生成规则：",
                "",
                "- 每个 active 项目都生成一个今日目标。",
                "",
                "## 用户偏好",
                "",
                "- 小目标。",
            ]
        ),
        encoding="utf-8",
    )


def _seed_monday_to_thursday(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful DayPilot MVP.",
                current_focus_projects=["DayPilot smoke acceptance"],
                default_available_minutes=80,
            )
            for day_text, item in [
                ("2026-06-08", "today goal API"),
                ("2026-06-09", "check-in persistence"),
                ("2026-06-10", "goal feedback revision"),
                ("2026-06-11", "weekly report aggregation"),
            ]:
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date=day_text,
                    context_snapshot={"source": "frontend-api-smoke"},
                    generated_at=f"{day_text} 09:00:00",
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal=f"完成 DayPilot {item} 的可验收切片",
                    goal_reason=f"{item} 支撑 MVP smoke 验收。",
                    success_criteria=[f"交付 {item}", "记录验收结果"],
                    estimated_minutes=70,
                    difficulty_level=3,
                    minimum_version=f"{item} 有可检查记录。",
                    stretch_challenge="补充一条回归测试。",
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                repo.create_daily_checkin(
                    connection,
                    daily_goal_id=daily_goal_id,
                    checkin_date=day_text,
                    week_id=WEEK_ID,
                    completion_text=f"完成 {item}，留下可复查记录。",
                    felt_difficulty=3,
                    tomorrow_direction="继续 DayPilot smoke 验收",
                    parsed_completion_rate=0.9,
                    completed_items=[item],
                    unfinished_items=[],
                    blockers=[],
                    actual_outputs=[f"smoke/{item}"],
                    processor_snapshot={"source": "frontend-api-smoke"},
                    created_at=f"{day_text} 00:00:00",
                    updated_at=f"{day_text} 00:00:00",
                )
            repo.create_ability_state(
                connection,
                state_date="2026-06-11",
                current_difficulty=3.0,
                target_difficulty_level=3,
                recent_completion_rate=0.9,
                recent_felt_difficulty_avg=3.0,
                default_estimated_minutes=80,
                preferred_goal_type_weights={"coding": 0.7, "testing": 0.3},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["目标太大", "周报流水账"],
                adjustment_direction="hold",
                update_reason="Smoke seed.",
                is_current=1,
            )
    finally:
        connection.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "frontend-api-smoke.sqlite3"
        soul_path = _soul_file(Path(temp_dir))
        _seed_monday_to_thursday(db_path)

        frontend_server, frontend_thread, frontend_base = _start_static_frontend()
        backend_server, backend_thread, backend_base = _start_backend(db_path, soul_path)
        try:
            homepage = _read_text(f"{frontend_base}/pages/index.html")
            for marker in [
                'id="today-view"',
                'id="history-view"',
                'id="weekly-view"',
                'id="career-view"',
                'id="history-list"',
                'id="weekly-feedback-form"',
                'id="weekly-report-versions"',
                'id="career-chat-form"',
                'id="career-message-list"',
                'id="app-alert"',
                'id="goal-card"',
                'id="checkin-form"',
                'id="goal-feedback-form"',
                'id="weekly-report-generate"',
                'id="weekly-completed-work"',
                'id="weekly-next-plan"',
                'id="weekly-reflection"',
                "Check-in 仅在完成当天可以修改，过后只展示最新可用版本。",
            ]:
                assert marker in homepage, f"homepage missing {marker}"
            assert "model_name" not in homepage
            assert "llm_metadata" not in homepage
            assert 'id="career-available-minutes"' not in homepage
            assert 'id="project-update-open"' not in homepage
            assert 'id="project-modal"' not in homepage
            assert 'id="project-lifecycle-form"' not in homepage
            assert "项目更新" not in homepage
            assert "今天可投入分钟数" not in homepage
            frontend_js = _read_text(f"{frontend_base}/services/today-goal.js")
            for marker in [
                "checkedInGoalIds",
                "renderVisibleTodayGoalCards",
                "handleCareerChatSubmit",
                "importSoulProjectsBeforeRefresh",
                "/api/soul-sync/import-projects",
                "careerRecommendationsBlock",
                "今天的项目都已 check-in",
            ]:
                assert marker in frontend_js, f"frontend JS missing {marker}"
            assert "available_minutes" not in frontend_js
            assert "/api/projects/lifecycle" not in frontend_js
            assert "/api/career-chat/profile-suggestion" not in frontend_js
            assert "career-profile-suggestions" not in homepage

            status, career_chat = _post_json(
                f"{backend_base}/api/career-chat",
                {
                    "message": "我会 Python 和一点机器学习，想往 AI Agent 方向发展，应该做什么项目？",
                    "available_minutes": 45,
                },
            )
            assert status == 200
            assert career_chat["session_id"] > 0
            assert isinstance(career_chat["recommendations"], list)
            assert career_chat["profile_update_suggestions"]
            assert {item["status"] for item in career_chat["profile_update_suggestions"]} == {"applied"}
            assert career_chat["career_profile_update"]["status"] == "applied"
            status, career_history = _get_json(
                f"{backend_base}/api/career-chat/history?session_id={career_chat['session_id']}"
            )
            assert status == 200
            assert len(career_history["messages"]) == 2
            assert career_history["pending_profile_update_suggestions"] == []

            _write_soul_current_projects(
                soul_path,
                "\n".join(
                    [
                        "1. DayPilot smoke acceptance：当前进度：准备 smoke 验收。项目今日目标：完成 DayPilot smoke 验收。",
                        "2. 微调一个编排规则的模型：当前进度：还没确定实现方案。项目最终目标：形成可复查的规则编排微调方案。项目今日目标：先确定方案。",
                    ]
                ),
            )
            status, project_create = _post_json(f"{backend_base}/api/soul-sync/import-projects", {})
            assert status == 200
            assert project_create["status"] == "applied"
            assert project_create["created_count"] >= 1

            status, projects_payload = _get_json(f"{backend_base}/api/projects")
            assert status == 200
            assert any(project["name"] == "微调一个编排规则的模型" for project in projects_payload["active_projects"])

            status, today_payload = _get_json(f"{backend_base}/api/today-goal")
            assert status == 200
            assert today_payload["is_workday"] is True
            assert today_payload["active_project_count"] == len(projects_payload["active_projects"])
            assert len(today_payload["goals"]) == len(projects_payload["active_projects"])
            assert all(goal["daily_goal"]["goal_date"] == FRIDAY.isoformat() for goal in today_payload["goals"])
            goal_id = today_payload["goals"][0]["daily_goal"]["id"]

            status, feedback_payload = _post_json(
                f"{backend_base}/api/goal-feedback",
                {
                    "date": FRIDAY.isoformat(),
                    "goal_id": goal_id,
                    "message": "今天只有 40 分钟，请缩小范围并写清完成标准。",
                },
            )
            assert status == 200
            assert feedback_payload["updated_goal"]["active_version"]["version_no"] >= 2
            assert feedback_payload["updated_goal"]["goal_output"]["estimated_minutes"] <= 40
            assert feedback_payload["memory_update"]["status"] in {"applied", "skipped", "failed"}

            status, checkin_payload = _post_json(
                f"{backend_base}/api/checkin",
                {
                    "date": FRIDAY.isoformat(),
                    "goal_id": goal_id,
                    "completion_text": "完成 smoke 验收目标，三段式周报可以生成。",
                    "felt_difficulty": 3,
                    "tomorrow_direction": "下周继续 weekly_focus 承接验证",
                },
            )
            assert status == 200
            for index, goal_record in enumerate(today_payload["goals"][1:], start=2):
                status, checkin_payload = _post_json(
                    f"{backend_base}/api/checkin",
                    {
                        "date": FRIDAY.isoformat(),
                        "goal_id": goal_record["daily_goal"]["id"],
                        "completion_text": f"完成 smoke 验收目标 {index}，三段式周报可以生成。",
                        "felt_difficulty": 3,
                        "tomorrow_direction": "下周继续 weekly_focus 承接验证",
                    },
                )
                assert status == 200
            assert checkin_payload["can_generate_weekly_report"] is True
            assert checkin_payload["project_progress_update"]["status"] == "updated"

            status, report_payload = _post_json(
                f"{backend_base}/api/weekly-report/generate",
                {"week_id": WEEK_ID},
            )
            assert status == 200
            report_output = report_payload["report_output"]
            assert set(report_output) == {"completed_work", "next_week_plan", "weekly_reflection"}
            assert all(report_output[section] for section in report_output)
            assert len(report_payload["weekly_focus"]) >= 2
            assert len(report_payload["weekly_report_versions"]) == 1

            status, history_payload = _get_json(f"{backend_base}/api/history?days=7")
            assert status == 200
            assert len(history_payload["daily_records"]) >= 5
            assert history_payload["weekly_reports"][0]["weekly_report"]["week_id"] == WEEK_ID

            wednesday = next(
                record
                for record in history_payload["daily_records"]
                if record["daily_goal"]["goal_date"] == "2026-06-10"
            )
            assert wednesday["checkin_editable"] is False
            assert wednesday["checkin_edit_lock_reason"] == "已过提交当天，仅展示最新可用版本。"
            friday = next(
                record
                for record in history_payload["daily_records"]
                if record["daily_goal"]["id"] == goal_id
            )
            assert friday["checkin_editable"] is True
            assert friday["daily_checkin"] is not None
            assert friday["daily_goal"]["status"] == "checked_in"
            status, edit_payload = _post_json(
                f"{backend_base}/api/checkin",
                {
                    "date": FRIDAY.isoformat(),
                    "goal_id": friday["daily_goal"]["id"],
                    "completion_text": "Edited smoke check-in with a clearer artifact trail.",
                    "felt_difficulty": 2,
                    "tomorrow_direction": "Keep the weekly report revision path tight.",
                },
            )
            assert status == 200
            assert edit_payload["updated"] is True
            assert edit_payload["weekly_report_refresh"]["status"] == "refreshed"
            status, edited_history_payload = _get_json(f"{backend_base}/api/history?days=7")
            assert status == 200
            edited_friday = next(
                record
                for record in edited_history_payload["daily_records"]
                if record["daily_goal"]["id"] == goal_id
            )
            assert edited_friday["checkin_editable"] is True
            assert edited_friday["daily_goal"]["status"] == "checked_in"
            assert edited_friday["daily_checkin"]["completion_text"] == "Edited smoke check-in with a clearer artifact trail."

            status, feedback_report = _post_json(
                f"{backend_base}/api/weekly-report/feedback",
                {
                    "week_id": WEEK_ID,
                    "message": "Make next week items more directly verifiable.",
                },
            )
            assert status == 200
            assert feedback_report["created"] is False
            assert len(feedback_report["weekly_report_versions"]) >= 3

            _write_soul_current_projects(
                soul_path,
                "1. DayPilot smoke acceptance：当前进度：smoke 已验证。项目今日目标：继续保持验收闭环。",
            )
            status, project_complete = _post_json(f"{backend_base}/api/soul-sync/import-projects", {})
            assert status == 200
            assert project_complete["status"] == "applied"
            assert project_complete["completed_count"] >= 1
        finally:
            _stop_server(backend_server, backend_thread)
            _stop_server(frontend_server, frontend_thread)

    print("PASS: frontend/API smoke covers compact UI, SOUL project sync, history edit, and weekly report revisions")


if __name__ == "__main__":
    main()
