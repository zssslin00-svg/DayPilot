from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api.server import create_server  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(db_path: Path, method: str, path: str) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    server = create_server("127.0.0.1", port, db_path=db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_soul_sync_status_and_retry_api() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "soul-sync-api.sqlite3"
        connection = initialize_database(db_path)
        connection.close()

        status_code, status_payload = _request(db_path, "GET", "/api/soul-sync/status")
        retry_code, retry_payload = _request(db_path, "POST", "/api/soul-sync/retry")

        assert status_code == 200
        assert status_payload["counts"]["pending"] == 0
        assert status_payload["counts"]["failed"] == 0
        assert retry_code == 200
        assert retry_payload["retried"] == 0
        assert retry_payload["status"]["counts"]["pending"] == 0


def main() -> None:
    test_soul_sync_status_and_retry_api()
    print("PASS: SOUL sync status and retry API routes respond")


if __name__ == "__main__":
    main()
