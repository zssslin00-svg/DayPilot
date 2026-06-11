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


TODAY = date(2026, 6, 11)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "# DayPilot SOUL\n\n## 长期方向\n\n项目驱动成长。\n\n## 当前项目\n\n无。\n",
        encoding="utf-8",
    )
    return path


def _seed_profile(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build AI Agent skills through real projects.",
                career_profile={"current_skills": ["Python"]},
            )
    finally:
        connection.close()


def _start_server(db_path: Path, soul_path: Path) -> tuple[Any, threading.Thread, str]:
    port = _free_port()
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: TODAY,
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


def _request(method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as response:
        return int(response.status), json.loads(response.read().decode("utf-8"))


def test_career_chat_api_endpoints() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-api.sqlite3"
        soul_path = _soul_file(root)
        _seed_profile(db_path)
        server, thread, base = _start_server(db_path, soul_path)
        try:
            status, chat = _request(
                "POST",
                f"{base}/api/career-chat",
                {
                    "message": "我会 Python，想往 AI Agent 方向发展，适合做什么项目？",
                    "available_minutes": 45,
                },
            )
            assert status == 200
            assert chat["session_id"] > 0
            assert chat["assistant_message"]["role"] == "assistant"
            assert isinstance(chat["recommendations"], list)
            assert chat["profile_update_suggestions"]

            status, sessions = _request("GET", f"{base}/api/career-chat/sessions")
            assert status == 200
            assert sessions["sessions"][0]["id"] == chat["session_id"]

            status, history = _request("GET", f"{base}/api/career-chat/history?session_id={chat['session_id']}")
            assert status == 200
            assert len(history["messages"]) == 2
            assert history["pending_profile_update_suggestions"]

            suggestion_id = history["pending_profile_update_suggestions"][0]["id"]
            status, applied = _request(
                "POST",
                f"{base}/api/career-chat/profile-suggestion",
                {"suggestion_id": suggestion_id, "decision": "apply"},
            )
            assert status == 200
            assert applied["status"] == "applied"
            assert applied["career_profile"]

            status, second_chat = _request(
                "POST",
                f"{base}/api/career-chat",
                {
                    "session_id": chat["session_id"],
                    "message": "我可能也有点焦虑，先不要保存这个判断。",
                },
            )
            assert status == 200
            status, second_history = _request(
                "GET",
                f"{base}/api/career-chat/history?session_id={chat['session_id']}",
            )
            assert status == 200
            pending = second_history["pending_profile_update_suggestions"]
            assert pending
            status, dismissed = _request(
                "POST",
                f"{base}/api/career-chat/profile-suggestion",
                {"suggestion_id": pending[0]["id"], "decision": "dismiss"},
            )
            assert status == 200
            assert dismissed["status"] == "dismissed"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_career_chat_api_rejects_empty_message() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-api-invalid.sqlite3"
        soul_path = _soul_file(root)
        server, thread, base = _start_server(db_path, soul_path)
        try:
            status, payload = _request("POST", f"{base}/api/career-chat", {"message": ""})
            assert status == 400
            assert payload["error"] == "invalid_career_chat"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def main() -> None:
    test_career_chat_api_endpoints()
    test_career_chat_api_rejects_empty_message()
    print("PASS: career chat API endpoints respond and persist local chat state")


if __name__ == "__main__":
    main()
