from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import (
    PROJECT_ROOT,
    default_backup_dir,
    default_data_dir,
    default_llm_log_dir,
    default_tmp_dir,
)
from backend.repositories.database import DEFAULT_DB_PATH
from backend.services.soul_context import SOUL_PATH
from backend.services.soul_sync_service import retry_soul_sync_jobs


DATA_DIR = default_data_dir()
BACKUP_DIR = default_backup_dir()
LLM_LOG_DIR = default_llm_log_dir()
TMP_DIR = default_tmp_dir()

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
    root: str | Path | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    cleanup_result = cleanup_runtime_data(now=current, root=root)
    retry_result = retry_soul_sync_jobs(db_path, soul_path=soul_path).payload
    return {
        "cleanup": cleanup_result,
        "soul_sync_retry": retry_result,
    }


def cleanup_runtime_data(
    *,
    now: datetime | None = None,
    root: str | Path | None = PROJECT_ROOT,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    if data_dir is not None:
        runtime_data_dir = Path(data_dir)
    elif root is None:
        runtime_data_dir = DATA_DIR
    else:
        runtime_data_dir = Path(root) / "data"
    backup_dir = runtime_data_dir / "backups"
    llm_log_dir = runtime_data_dir / "llm_logs"
    tmp_dir = runtime_data_dir / "tmp"

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
