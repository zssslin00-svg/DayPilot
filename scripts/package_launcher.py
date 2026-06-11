from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import threading
import time
import webbrowser
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


APP_NAME = "DayPilot"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 5173
LOCAL_OPENER = build_opener(ProxyHandler({}))


class PackageLauncherError(RuntimeError):
    pass


class NoCacheStaticHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):  # type: ignore[no-untyped-def]
        for header in ("If-Modified-Since", "If-None-Match"):
            if header in self.headers:
                del self.headers[header]
        return super().send_head()


def bundled_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[1]


def default_user_data_dir() -> Path:
    override = os.environ.get("DAYPILOT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / "daypilot"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run packaged DayPilot.")
    parser.add_argument("--data-dir", help="User data directory. Defaults to the OS application data folder.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for local backend and frontend servers.")
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Start without opening the browser.")
    return parser.parse_args()


def prepare_user_runtime(app_root: Path, data_dir: Path) -> dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    for child in ("db", "backups", "tmp", "llm_logs"):
        (data_dir / child).mkdir(parents=True, exist_ok=True)

    soul_path = data_dir / "SOUL.md"
    env_path = data_dir / ".env"
    schema_path = app_root / "scripts" / "init_db.sql"
    frontend_dir = app_root / "frontend"

    if not schema_path.exists():
        raise PackageLauncherError(f"Missing bundled database schema: {schema_path}")
    if not frontend_dir.exists():
        raise PackageLauncherError(f"Missing bundled frontend directory: {frontend_dir}")

    if not soul_path.exists():
        source = app_root / "SOUL.example.md"
        if source.exists():
            shutil.copy2(source, soul_path)
        else:
            soul_path.write_text("# DayPilot SOUL\n", encoding="utf-8")

    if not env_path.exists():
        source = app_root / ".env.example"
        if source.exists():
            text = source.read_text(encoding="utf-8")
            text = text.replace("DAYPILOT_LLM_MODE=deepseek", "DAYPILOT_LLM_MODE=mock")
            env_path.write_text(text, encoding="utf-8")
        else:
            env_path.write_text(
                "\n".join(
                    [
                        "DAYPILOT_LLM_MODE=mock",
                        "DAYPILOT_LLM_LOG_ENABLED=true",
                        "DEEPSEEK_API_KEY=",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

    os.environ.setdefault("DAYPILOT_DATA_DIR", str(data_dir))
    os.environ.setdefault("DAYPILOT_SOUL_PATH", str(soul_path))
    os.environ.setdefault("DAYPILOT_ENV_PATH", str(env_path))
    os.environ.setdefault("DAYPILOT_SCHEMA_PATH", str(schema_path))
    os.environ.setdefault("DAYPILOT_LLM_LOG_DIR", str(data_dir / "llm_logs"))

    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    return {
        "data_dir": data_dir,
        "db_path": data_dir / "db" / "daypilot.sqlite3",
        "backup_dir": data_dir / "backups",
        "tmp_dir": data_dir / "tmp",
        "soul_path": soul_path,
        "env_path": env_path,
        "schema_path": schema_path,
        "frontend_dir": frontend_dir,
    }


def backup_existing_database(db_path: Path, backup_dir: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"daypilot_{stamp}.sqlite3"
    shutil.copy2(db_path, target)
    return target


def wait_for_url(url: str, *, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with LOCAL_OPENER.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    detail = f" Last error: {last_error}" if last_error else ""
    raise PackageLauncherError(f"Timed out waiting for {url}.{detail}")


def serve_frontend(frontend_dir: Path, host: str, port: int) -> ThreadingHTTPServer:
    handler = partial(NoCacheStaticHandler, directory=str(frontend_dir))
    return ThreadingHTTPServer((host, port), handler)


def run_server_thread(server: ThreadingHTTPServer, name: str) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    thread.start()
    return thread


def main() -> None:
    args = parse_args()
    app_root = bundled_root()
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_user_data_dir()

    try:
        paths = prepare_user_runtime(app_root, data_dir)
        backup_path = backup_existing_database(paths["db_path"], paths["backup_dir"])

        from backend.api.server import create_server
        from backend.repositories.database import initialize_database
        from backend.services.runtime_maintenance_service import start_background_maintenance

        connection = initialize_database(paths["db_path"], schema_path=paths["schema_path"])
        connection.close()

        backend = create_server(
            args.host,
            args.backend_port,
            db_path=paths["db_path"],
            soul_path=paths["soul_path"],
        )
        frontend = serve_frontend(paths["frontend_dir"], args.host, args.frontend_port)
        maintenance = start_background_maintenance(
            db_path=paths["db_path"],
            soul_path=paths["soul_path"],
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    stop_event = threading.Event()

    def request_stop(signum: int | None = None, frame: object | None = None) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
    except (AttributeError, ValueError):
        pass

    backend_url = f"http://{args.host}:{args.backend_port}"
    frontend_url = f"http://{args.host}:{args.frontend_port}/pages/index.html"

    run_server_thread(backend, "daypilot-backend")
    run_server_thread(frontend, "daypilot-frontend")

    try:
        wait_for_url(f"{backend_url}/health", timeout_seconds=15)
        wait_for_url(frontend_url, timeout_seconds=8)
        if not args.no_browser:
            webbrowser.open(f"{frontend_url}?v={int(time.time())}")

        print(f"DayPilot backend: {backend_url}")
        print(f"DayPilot frontend: {frontend_url}")
        print(f"DayPilot data: {paths['data_dir']}")
        print(f"DayPilot profile: {paths['soul_path']}")
        if backup_path is not None:
            print(f"Backed up existing database to {backup_path}")
        print("Press Ctrl+C or close this terminal to stop DayPilot.")

        while not stop_event.wait(0.5):
            pass
    finally:
        maintenance.stop_event.set()
        backend.shutdown()
        frontend.shutdown()
        backend.server_close()
        frontend.server_close()


if __name__ == "__main__":
    main()
