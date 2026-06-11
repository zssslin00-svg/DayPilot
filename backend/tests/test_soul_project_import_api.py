from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.api.server import create_server  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402


WORKDAY = date(2026, 6, 9)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    db_path: Path,
    soul_path: Path,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: WORKDAY,
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with opener.open(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_soul(path: Path, current_projects: str | None = None) -> None:
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                current_projects
                or "\n".join(
                    [
                        "1. P0 Alpha 项目：当前进度：新的 Alpha 进度。项目最终目标：确认 Alpha 最小闭环。项目今日目标：整理 Alpha 验收清单。",
                        "2. P1 Beta 项目：当前进度：刚开始。项目最终目标：写出 Beta 方案。项目今日目标：写出 Beta 第一版结构。",
                    ]
                ),
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


def _seed_db(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily loop.",
                current_focus_projects=["Alpha 项目"],
                goal_preferences={"project_priorities": []},
            )
            repo.create_project(
                connection,
                id=1,
                name="Alpha 项目",
                priority="P0",
                role="主线",
                status="active",
                status_summary="旧 Alpha 进度。",
                planning_bias="旧 Alpha 规划。",
                source_payload={},
            )
    finally:
        connection.close()


def test_soul_project_import_api_updates_projects_and_today_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-import-api.sqlite3"
        soul_path = root / "SOUL.md"
        _seed_db(db_path)
        _write_soul(soul_path)

        status, imported = _request(db_path, soul_path, "POST", "/api/soul-sync/import-projects")
        assert status == 200
        assert imported["status"] == "applied"
        assert imported["created_count"] == 1
        assert imported["updated_count"] == 1

        status, projects = _request(db_path, soul_path, "GET", "/api/projects")
        assert status == 200
        active_names = [project["name"] for project in projects["active_projects"]]
        assert active_names == ["Alpha 项目", "Beta 项目"]

        status, today = _request(db_path, soul_path, "GET", "/api/today-goal")
        assert status == 200
        assert today["is_workday"] is True
        assert today["active_project_count"] == 2
        assert len(today["goals"]) == 2
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "项目最终目标：确认 Alpha 最小闭环" in soul_text
        assert "项目今日目标：" in soul_text


def test_today_goal_auto_imports_soul_projects_before_generation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-auto-import-api.sqlite3"
        soul_path = root / "SOUL.md"
        _write_soul(soul_path)

        status, today = _request(db_path, soul_path, "GET", "/api/today-goal")
        assert status == 200
        assert today["is_workday"] is True
        assert today["soul_project_import"]["status"] == "applied"
        assert today["soul_project_import"]["created_count"] == 2
        assert today["active_project_count"] == 2
        assert [goal["project"]["name"] for goal in today["goals"]] == ["Alpha 项目", "Beta 项目"]


def test_regenerate_today_goal_imports_latest_soul_first() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-regenerate-import-api.sqlite3"
        soul_path = root / "SOUL.md"
        _write_soul(
            soul_path,
            "1. P0 Alpha 项目：当前进度：旧进度。项目最终目标：确认 Alpha 最小闭环。项目今日目标：整理 Alpha 验收清单。",
        )

        status, first_today = _request(db_path, soul_path, "GET", "/api/today-goal")
        assert status == 200
        assert first_today["active_project_count"] == 1

        _write_soul(
            soul_path,
            "\n".join(
                [
                    "1. P0 Alpha 项目：当前进度：新的 Alpha 进度。项目最终目标：确认 Alpha 最小闭环。项目今日目标：整理 Alpha 验收清单。",
                    "2. P1 Beta 项目：当前进度：刚开始。项目最终目标：写出 Beta 方案。项目今日目标：写出 Beta 第一版结构。",
                ]
            ),
        )
        status, regenerated = _request(db_path, soul_path, "POST", "/api/today-goal/regenerate")
        assert status == 200
        assert regenerated["soul_project_import"]["status"] == "applied"
        assert regenerated["soul_project_import"]["created_count"] == 1
        assert regenerated["soul_project_import"]["updated_count"] == 1
        assert regenerated["active_project_count"] == 2


def main() -> None:
    test_soul_project_import_api_updates_projects_and_today_goal()
    test_today_goal_auto_imports_soul_projects_before_generation()
    test_regenerate_today_goal_imports_latest_soul_first()
    print("PASS: SOUL.md project import API updates projects and today goals")


if __name__ == "__main__":
    main()
