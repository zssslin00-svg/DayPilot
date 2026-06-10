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


def _post_feedback(today: date, db_path: Path, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    soul_path = _soul_file(db_path.parent)
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/goal-feedback",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
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


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 长期方向",
                "",
                "长期方向。",
                "",
                "## 当前项目",
                "",
                "测试项目段落。",
                "",
                "## 用户偏好",
                "",
                "用户更喜欢：",
                "",
                "- 小而可交付的目标。",
                "",
                "## 避免事项",
                "",
                "生成目标时要避免：",
                "",
                "- 不要把长期愿望压成一天任务。",
                "",
                "## 时间预算与目标数量",
                "- 用户每天有效工作时间约为 4 小时。",
                "",
                "## 每日目标原则",
                "",
                "每日目标原则。",
                "",
                "## 反馈修正规则",
                "",
                "反馈规则。",
                "",
                "## 周报原则",
                "",
                "周报规则。",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_time_limit_feedback_creates_shorter_active_version() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "goal-feedback-time.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-08", goal_type="design")

        status, payload = _post_feedback(
            date(2026, 6, 8),
            db_path,
            {"date": "2026-06-08", "goal_id": goal_id, "message": "今天只有 40 分钟"},
        )

        assert status == 200
        updated_output = payload["updated_goal"]["goal_output"]
        validate_daily_goal_output(updated_output)
        assert updated_output["estimated_minutes"] == 40
        assert len(updated_output["completion_criteria"]) <= 2
        assert payload["feedback_signal"]["primary_feedback_type"] == "time_limit"
        assert payload["feedback_message"]["feedback_type"] == "day_constraint"
        assert payload["memory_update"]["status"] == "skipped"

        connection = connect_database(db_path)
        try:
            versions = repo.list_goal_versions(connection, goal_id)
            assert len(versions) == 2
            assert versions[-1]["is_active"] == 1
            assert versions[-1]["critic_result"]["review"]["passed"] is True
            assert versions[-1]["critic_result"]["llm_metadata"]["llm_mode_used"] == "mock"
            assert repo.get_daily_goal(connection, goal_id)["revision_count"] == 1
        finally:
            connection.close()


def test_scope_too_large_feedback_shrinks_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "goal-feedback-large.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-09", goal_type="design")

        status, payload = _post_feedback(
            date(2026, 6, 9),
            db_path,
            {"date": "2026-06-09", "goal_id": goal_id, "message": "这个目标太大了，今天做不完"},
        )

        assert status == 200
        updated_output = payload["updated_goal"]["goal_output"]
        assert updated_output["main_goal"].startswith("缩小范围：")
        assert updated_output["difficulty"] == 2
        assert payload["feedback_signal"]["primary_feedback_type"] == "scope_too_large"
        assert payload["memory_update"]["status"] in {"skipped", "failed"}
        validate_daily_goal_output(updated_output)


def test_prefers_coding_feedback_switches_goal_type_and_saves_memory_signal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "goal-feedback-coding.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-10", goal_type="design")

        status, payload = _post_feedback(
            date(2026, 6, 10),
            db_path,
            {"date": "2026-06-10", "goal_id": goal_id, "message": "我更想写代码"},
        )

        assert status == 200
        updated_output = payload["updated_goal"]["goal_output"]
        assert updated_output["goal_type"] == "coding"
        assert "代码" in updated_output["main_goal"]
        assert payload["feedback_message"]["feedback_type"] == "short_term_preference"
        assert payload["memory_update"]["action"] == "update_short_term_preference"
        assert payload["memory_update"]["status"] in {"applied", "skipped", "failed"}
        validate_daily_goal_output(updated_output)


def test_goal_feedback_rejects_empty_message() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "goal-feedback-invalid.sqlite3"
        goal_id = _seed_goal(db_path, "2026-06-11", goal_type="design")

        status, payload = _post_feedback(
            date(2026, 6, 11),
            db_path,
            {"date": "2026-06-11", "goal_id": goal_id, "message": ""},
        )

        assert status == 400
        assert payload["error"] == "invalid_goal_feedback"


def _seed_goal(db_path: Path, goal_date: str, *, goal_type: str) -> int:
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
                context_snapshot={
                    "goal_output_context_used": {
                        "primary_driver": "current_project",
                        "tomorrow_direction_handling": "empty_agent_decided",
                        "continuity_note": "Seeded test goal.",
                        "difficulty_reason": "Seeded difficulty.",
                    }
                },
                generated_at=f"{goal_date} 09:00:00",
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=daily_goal_id,
                version_no=1,
                is_active=1,
                main_goal="Design the DayPilot feedback flow deliverable.",
                goal_reason="A clear feedback flow is needed before revision can be tested.",
                success_criteria=[
                    "Document current active goal fields",
                    "Describe feedback signal mapping",
                    "List persistence updates",
                ],
                estimated_minutes=90,
                difficulty_level=3,
                minimum_version="A feedback flow design note exists.",
                stretch_challenge="Add a mock revision example.",
                avoid_today=json.dumps(["Do not implement weekly reports"], ensure_ascii=False),
                goal_type=goal_type,
                revision_source="initial_generation",
            )
            return daily_goal_id
    finally:
        connection.close()


def main() -> None:
    test_time_limit_feedback_creates_shorter_active_version()
    test_scope_too_large_feedback_shrinks_goal()
    test_prefers_coding_feedback_switches_goal_type_and_saves_memory_signal()
    test_goal_feedback_rejects_empty_message()
    print("PASS: POST /api/goal-feedback revises goals and saves feedback history")


if __name__ == "__main__":
    main()
