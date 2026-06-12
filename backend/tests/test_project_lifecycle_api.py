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
from backend.repositories.database import initialize_database  # noqa: E402


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
        today_provider=lambda: date(2026, 6, 9),
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        if body is None:
            with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with opener.open(request, timeout=5) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "# DayPilot SOUL\n\n## 当前项目\n\n旧项目段落\n\n## 用户偏好\n\n- 小目标。\n",
        encoding="utf-8",
    )
    return path


def _seed(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily loop.",
                current_focus_projects=["DayPilot 日用验证"],
                goal_preferences={"project_priorities": []},
            )
            repo.create_project(
                connection,
                id=1,
                name="DayPilot 日用验证",
                priority="P0",
                role="主线",
                status="active",
                status_summary="正在验证日常使用。",
                planning_bias="优先安排真实使用和阻塞修复。",
                source_payload={},
            )
    finally:
        connection.close()


def test_project_lifecycle_api_is_read_only_and_rejects_user_writes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "api-lifecycle.sqlite3"
        soul_path = _soul_file(root)
        _seed(db_path)

        status, overview = _request(db_path, soul_path, "GET", "/api/projects")
        assert status == 200
        assert overview["active_projects"][0]["name"] == "DayPilot 日用验证"

        status, disabled = _request(
            db_path,
            soul_path,
            "POST",
            "/api/projects/lifecycle",
            {
                "message": "新增 P0 项目：微调一个编排规则的模型。当前进度：还没确定实现方案。项目最终目标：形成可复查的规则编排微调方案。项目今日目标：先确定方案。",
            },
        )
        assert status == 410
        assert disabled["error"] == "project_lifecycle_disabled"

        status, overview = _request(db_path, soul_path, "GET", "/api/projects")
        active_names = [project["name"] for project in overview["active_projects"]]
        completed_names = [project["name"] for project in overview["completed_projects"]]
        assert "微调一个编排规则的模型" not in active_names
        assert "DayPilot 日用验证" in active_names
        assert "DayPilot 日用验证" not in completed_names
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "微调一个编排规则的模型" not in soul_text


def main() -> None:
    test_project_lifecycle_api_is_read_only_and_rejects_user_writes()
    print("PASS: project lifecycle API lists projects and rejects user writes")


if __name__ == "__main__":
    main()
