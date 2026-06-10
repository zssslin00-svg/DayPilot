from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.repositories.database import DEFAULT_DB_PATH
from backend.services.soul_context import SOUL_PATH
from backend.services.soul_sync_service import retry_soul_sync_jobs


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
LLM_LOG_DIR = PROJECT_ROOT / "data" / "llm_logs"
TMP_DIR = PROJECT_ROOT / "data" / "tmp"

ONE_DAY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class MaintenanceHandle:
    stop_event: threading.Event
    thread: threading.Thread


def run_runtime_maintenance(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    soul_path: str | Path = SOUL_PATH,
    now: datetime | None = None,
    root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    current = now or datetime.now()
    project_root = Path(root)
    cleanup_result = cleanup_runtime_data(now=current, root=project_root)
    retry_result = retry_soul_sync_jobs(db_path, soul_path=soul_path).payload
    return {
        "cleanup": cleanup_result,
        "soul_sync_retry": retry_result,
    }


def cleanup_runtime_data(
    *,
    now: datetime | None = None,
    root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    current = now or datetime.now()
    project_root = Path(root)
    backup_dir = project_root / "data" / "backups"
    llm_log_dir = project_root / "data" / "llm_logs"
    tmp_dir = project_root / "data" / "tmp"

    deleted: dict[str, list[str]] = {
        "database_backups": [],
        "soul_backups": [],
        "llm_logs": [],
        "tmp_files": [],
    }
    deleted["database_backups"] = _delete_old_with_minimum(
        sorted(backup_dir.glob("*.sqlite3"), key=_mtime, reverse=True),
        cutoff=current - timedelta(days=30),
        keep_minimum=10,
    )
    deleted["soul_backups"] = _delete_old_with_minimum(
        sorted(backup_dir.glob("SOUL_*.md"), key=_mtime, reverse=True),
        cutoff=current - timedelta(days=30),
        keep_minimum=20,
    )
    deleted["llm_logs"] = _delete_old_files(
        [path for path in llm_log_dir.glob("*/*.jsonl") if path.is_file()],
        cutoff=current - timedelta(days=14),
    )
    deleted["tmp_files"] = _delete_old_files(
        [
            path
            for path in tmp_dir.glob("*")
            if path.is_file() and path.suffix.lower() != ".pid"
        ],
        cutoff=current - timedelta(days=3),
    )
    return {
        "deleted_counts": {key: len(value) for key, value in deleted.items()},
        "deleted_files": deleted,
    }


def start_background_maintenance(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    soul_path: str | Path = SOUL_PATH,
    interval_seconds: int = ONE_DAY_SECONDS,
) -> MaintenanceHandle:
    stop_event = threading.Event()

    def worker() -> None:
        while not stop_event.is_set():
            try:
                run_runtime_maintenance(db_path=db_path, soul_path=soul_path)
            except Exception:
                pass
            stop_event.wait(interval_seconds)

    thread = threading.Thread(target=worker, name="daypilot-runtime-maintenance", daemon=True)
    thread.start()
    return MaintenanceHandle(stop_event=stop_event, thread=thread)


def _delete_old_with_minimum(paths: list[Path], *, cutoff: datetime, keep_minimum: int) -> list[str]:
    deleted: list[str] = []
    for index, path in enumerate(paths):
        if index < keep_minimum:
            continue
        if _mtime(path) >= cutoff:
            continue
        if _delete_file(path):
            deleted.append(str(path))
    return deleted


def _delete_old_files(paths: list[Path], *, cutoff: datetime) -> list[str]:
    deleted: list[str] = []
    for path in paths:
        if _mtime(path) >= cutoff:
            continue
        if _delete_file(path):
            deleted.append(str(path))
    return deleted


def _delete_file(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.max
