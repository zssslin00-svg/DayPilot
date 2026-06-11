from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daypilot_processes import (  # noqa: E402
    DayPilotProcessError,
    stop_daypilot_processes,
)

BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173/pages/index.html"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
DAYPILOT_PORTS = (BACKEND_PORT, FRONTEND_PORT)
LOCAL_OPENER = build_opener(ProxyHandler({}))


class StartupError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    state_dir: Path
    db_path: Path
    backup_dir: Path
    backend_pid_file: Path
    frontend_pid_file: Path
    backend_out_log: Path
    backend_err_log: Path
    frontend_out_log: Path
    frontend_err_log: Path
    env_path: Path
    soul_path: Path
    schema_path: Path
    llm_log_dir: Path


def runtime_paths(root: str | Path = ROOT) -> RuntimePaths:
    base = Path(root)
    data_dir = base / "data"
    state_dir = base / "data" / "tmp"
    return RuntimePaths(
        state_dir=state_dir,
        db_path=data_dir / "db" / "daypilot.sqlite3",
        backup_dir=data_dir / "backups",
        backend_pid_file=state_dir / "backend.pid",
        frontend_pid_file=state_dir / "frontend.pid",
        backend_out_log=state_dir / "backend.out.log",
        backend_err_log=state_dir / "backend.err.log",
        frontend_out_log=state_dir / "frontend.out.log",
        frontend_err_log=state_dir / "frontend.err.log",
        env_path=base / ".env",
        soul_path=base / "SOUL.md",
        schema_path=base / "scripts" / "init_db.sql",
        llm_log_dir=data_dir / "llm_logs",
    )


def build_development_environment(
    root: str | Path = ROOT,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    paths = runtime_paths(root)
    resolved = dict(os.environ if env is None else env)
    resolved.update(
        {
            "DAYPILOT_DATA_DIR": str(paths.db_path.parents[1]),
            "DAYPILOT_SOUL_PATH": str(paths.soul_path),
            "DAYPILOT_ENV_PATH": str(paths.env_path),
            "DAYPILOT_SCHEMA_PATH": str(paths.schema_path),
            "DAYPILOT_LLM_LOG_DIR": str(paths.llm_log_dir),
            "DAYPILOT_PREFER_DOTENV": "1",
        }
    )
    return resolved


def ensure_python_version() -> None:
    if sys.version_info < (3, 10):
        raise StartupError("DayPilot requires Python 3.10 or newer.")


def validate_deepseek_key(
    root: str | Path = ROOT,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    from backend.config.settings import load_daypilot_settings

    paths = runtime_paths(root)
    runtime_env = build_development_environment(root, env=env)
    settings = load_daypilot_settings(env=runtime_env, dotenv_path=paths.env_path)
    if settings.llm_mode == "mock":
        return
    if not settings.deepseek_api_key:
        raise StartupError(
            "DEEPSEEK_API_KEY is missing. Set it in .env or the environment before starting DayPilot."
        )


def backup_existing_database(
    db_path: str | Path,
    backup_dir: str | Path,
) -> Path | None:
    source = Path(db_path)
    if not source.exists():
        return None

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"daypilot_{stamp}.sqlite3"
    shutil.copy2(source, target)
    return target


def initialize_runtime_database(
    db_path: str | Path,
    *,
    schema_path: str | Path | None = None,
) -> None:
    from backend.repositories.database import initialize_database

    connection = initialize_database(Path(db_path), schema_path=schema_path or runtime_paths(ROOT).schema_path)
    connection.close()


def prepare_runtime(
    root: str | Path = ROOT,
    *,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    paths = runtime_paths(root)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    validate_deepseek_key(root, env=env)
    backup_path = backup_existing_database(paths.db_path, paths.backup_dir)
    initialize_runtime_database(paths.db_path, schema_path=paths.schema_path)
    return backup_path


def start_services(
    root: str | Path = ROOT,
    *,
    python_exe: str = sys.executable,
    open_browser: bool = True,
) -> tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]:
    base = Path(root)
    paths = runtime_paths(base)
    runtime_env = build_development_environment(base)
    paths.state_dir.mkdir(parents=True, exist_ok=True)

    backend_out = paths.backend_out_log.open("wb")
    backend_err = paths.backend_err_log.open("wb")
    frontend_out = paths.frontend_out_log.open("wb")
    frontend_err = paths.frontend_err_log.open("wb")
    try:
        backend = subprocess.Popen(
            [python_exe, "-u", "backend/api/server.py"],
            cwd=base,
            env=runtime_env,
            stdout=backend_out,
            stderr=backend_err,
        )
        frontend = subprocess.Popen(
            [python_exe, "-u", "scripts/serve_frontend.py"],
            cwd=base,
            env=runtime_env,
            stdout=frontend_out,
            stderr=frontend_err,
        )
    finally:
        backend_out.close()
        backend_err.close()
        frontend_out.close()
        frontend_err.close()

    paths.backend_pid_file.write_text(str(backend.pid), encoding="ascii")
    paths.frontend_pid_file.write_text(str(frontend.pid), encoding="ascii")

    try:
        wait_for_backend_health()
        wait_for_frontend()
    except Exception:
        stop_started_processes((backend, frontend))
        raise

    if open_browser:
        webbrowser.open(f"{FRONTEND_URL}?v={int(time.time())}")

    return backend, frontend


def wait_for_backend_health(timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with LOCAL_OPENER.open(f"{BACKEND_URL}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.5)

    message = "Backend health check failed at http://127.0.0.1:8000/health."
    if last_error is not None:
        message = f"{message} Last error: {last_error}"
    raise StartupError(message)


def wait_for_frontend(timeout_seconds: float = 8.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with LOCAL_OPENER.open(FRONTEND_URL, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.25)

    message = f"Frontend health check failed at {FRONTEND_URL}."
    if last_error is not None:
        message = f"{message} Last error: {last_error}"
    raise StartupError(message)


def stop_started_processes(processes: tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def stop_existing_daypilot_services(root: str | Path = ROOT) -> int:
    paths = runtime_paths(root)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    result = stop_daypilot_processes(
        pid_files=(paths.backend_pid_file, paths.frontend_pid_file),
        ports=DAYPILOT_PORTS,
        current_pid=os.getpid(),
    )
    return result.stopped_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start DayPilot backend and frontend.")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Stop existing DayPilot backend/frontend processes on ports 8000/5173 before starting.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start services without opening the browser.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        ensure_python_version()
        if args.restart:
            stopped_count = stop_existing_daypilot_services(ROOT)
            print(f"Stopped existing DayPilot processes: {stopped_count}")
        backup_path = prepare_runtime(ROOT)
        if backup_path is None:
            print("No existing database found; initialized a fresh DayPilot database.")
        else:
            print(f"Backed up existing database to {backup_path}")
        start_services(ROOT, open_browser=not args.no_browser)
    except StartupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except DayPilotProcessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"DayPilot backend: {BACKEND_URL}")
    print(f"DayPilot frontend: {FRONTEND_URL}")
    print("PID files and logs are under data/tmp/.")


if __name__ == "__main__":
    main()
