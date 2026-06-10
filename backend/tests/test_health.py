from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api.server import create_server  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    port = _free_port()
    server = create_server("127.0.0.1", port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print("PASS: GET /health returned {'status': 'ok'}")


if __name__ == "__main__":
    main()
