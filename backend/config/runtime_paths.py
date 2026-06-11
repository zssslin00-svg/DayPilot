from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser()
    return default


def default_data_dir(project_root: str | Path = PROJECT_ROOT) -> Path:
    return _path_from_env("DAYPILOT_DATA_DIR", Path(project_root) / "data")


def default_db_path(project_root: str | Path = PROJECT_ROOT) -> Path:
    return default_data_dir(project_root) / "db" / "daypilot.sqlite3"


def default_backup_dir(project_root: str | Path = PROJECT_ROOT) -> Path:
    return default_data_dir(project_root) / "backups"


def default_tmp_dir(project_root: str | Path = PROJECT_ROOT) -> Path:
    return default_data_dir(project_root) / "tmp"


def default_llm_log_dir(project_root: str | Path = PROJECT_ROOT) -> Path:
    return default_data_dir(project_root) / "llm_logs"


def default_env_path(project_root: str | Path = PROJECT_ROOT) -> Path:
    return _path_from_env("DAYPILOT_ENV_PATH", Path(project_root) / ".env")


def default_soul_path(project_root: str | Path = PROJECT_ROOT) -> Path:
    return _path_from_env("DAYPILOT_SOUL_PATH", Path(project_root) / "SOUL.md")


def default_schema_path(project_root: str | Path = PROJECT_ROOT) -> Path:
    return _path_from_env("DAYPILOT_SCHEMA_PATH", Path(project_root) / "scripts" / "init_db.sql")
