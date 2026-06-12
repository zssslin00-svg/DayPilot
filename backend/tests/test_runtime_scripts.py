from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config import runtime_paths as runtime_path_config  # noqa: E402
from scripts.daypilot_processes import looks_like_daypilot_process, stop_daypilot_processes  # noqa: E402
from scripts.package_launcher import prepare_user_runtime  # noqa: E402
from scripts.restore_db import latest_backup, restore_database  # noqa: E402
from scripts.start_daypilot import (  # noqa: E402
    StartupError,
    backup_existing_database,
    build_development_environment,
    initialize_runtime_database,
    prepare_runtime,
    runtime_paths,
    validate_deepseek_key,
)
from scripts.stop_daypilot import stop_daypilot  # noqa: E402


def test_start_stop_scripts_are_portable_and_keep_state_under_data_tmp() -> None:
    script_paths = [
        ROOT / "scripts" / "start_daypilot.py",
        ROOT / "scripts" / "stop_daypilot.py",
        ROOT / "scripts" / "daypilot_processes.py",
        ROOT / "scripts" / "serve_frontend.py",
        ROOT / "scripts" / "package_launcher.py",
        ROOT / "scripts" / "build_package.py",
        ROOT / "scripts" / "build_windows.py",
        ROOT / "scripts" / "build_macos.py",
        ROOT / "scripts" / "start_daypilot.bat",
        ROOT / "scripts" / "stop_daypilot.bat",
        ROOT / "scripts" / "restore_latest_db.bat",
    ]
    for path in script_paths:
        text = path.read_text(encoding="utf-8")
        assert "C:\\Users\\lin" not in text
        assert "codex-runtimes" not in text

    paths = runtime_paths(ROOT)
    assert paths.backend_pid_file == ROOT / "data" / "tmp" / "backend.pid"
    assert paths.frontend_pid_file == ROOT / "data" / "tmp" / "frontend.pid"
    assert paths.backend_out_log == ROOT / "data" / "tmp" / "backend.out.log"
    assert paths.frontend_err_log == ROOT / "data" / "tmp" / "frontend.err.log"
    assert paths.env_path == ROOT / ".env"
    assert paths.soul_path == ROOT / "SOUL.md"
    assert paths.schema_path == ROOT / "scripts" / "init_db.sql"
    assert paths.llm_log_dir == ROOT / "data" / "llm_logs"


def test_start_script_pins_development_environment_to_repo_paths() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source_env = {
            "DAYPILOT_DATA_DIR": "C:\\outside\\data",
            "DAYPILOT_SOUL_PATH": "C:\\outside\\SOUL.md",
            "DAYPILOT_ENV_PATH": "C:\\outside\\.env",
            "DAYPILOT_SCHEMA_PATH": "C:\\outside\\init_db.sql",
            "DAYPILOT_LLM_LOG_DIR": "C:\\outside\\logs",
            "DAYPILOT_LLM_MODE": "mock",
        }

        env = build_development_environment(root, env=source_env)

        assert env["DAYPILOT_DATA_DIR"] == str(root / "data")
        assert env["DAYPILOT_SOUL_PATH"] == str(root / "SOUL.md")
        assert env["DAYPILOT_ENV_PATH"] == str(root / ".env")
        assert env["DAYPILOT_SCHEMA_PATH"] == str(root / "scripts" / "init_db.sql")
        assert env["DAYPILOT_LLM_LOG_DIR"] == str(root / "data" / "llm_logs")
        assert env["DAYPILOT_PREFER_DOTENV"] == "1"
        assert env["DAYPILOT_LLM_MODE"] == "mock"


def test_daypilot_process_identity_covers_current_and_packaged_entrypoints() -> None:
    assert looks_like_daypilot_process("python backend/api/server.py")
    assert looks_like_daypilot_process("python scripts/serve_frontend.py")
    assert looks_like_daypilot_process("python -m http.server 5173 -d frontend")
    assert looks_like_daypilot_process("python scripts/package_launcher.py")
    assert looks_like_daypilot_process("D:\\apps\\DayPilot\\DayPilot.exe")
    assert not looks_like_daypilot_process("python other_project.py")
    assert not looks_like_daypilot_process("python -m http.server 5173 -d public")


def test_package_launcher_prepares_user_runtime_and_env_paths() -> None:
    keys = [
        "DAYPILOT_DATA_DIR",
        "DAYPILOT_SOUL_PATH",
        "DAYPILOT_ENV_PATH",
        "DAYPILOT_SCHEMA_PATH",
        "DAYPILOT_LLM_LOG_DIR",
        "DAYPILOT_PREFER_DOTENV",
    ]
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "app"
            data_dir = Path(temp_dir) / "user-data"
            (root / "scripts").mkdir(parents=True)
            (root / "frontend" / "pages").mkdir(parents=True)
            (root / "scripts" / "init_db.sql").write_text("-- schema\n", encoding="utf-8")
            (root / "frontend" / "pages" / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            (root / "SOUL.example.md").write_text("# Example SOUL\n", encoding="utf-8")
            (root / ".env.example").write_text(
                "DAYPILOT_LLM_MODE=deepseek\nDEEPSEEK_API_KEY=\n",
                encoding="utf-8",
            )

            paths = prepare_user_runtime(root, data_dir)

            assert paths["db_path"] == data_dir / "db" / "daypilot.sqlite3"
            assert paths["soul_path"].read_text(encoding="utf-8") == "# Example SOUL\n"
            assert "DAYPILOT_LLM_MODE=mock" in paths["env_path"].read_text(encoding="utf-8")
            assert runtime_path_config.default_db_path() == data_dir / "db" / "daypilot.sqlite3"
            assert runtime_path_config.default_soul_path() == data_dir / "SOUL.md"
            assert runtime_path_config.default_schema_path() == root / "scripts" / "init_db.sql"
            assert os.environ["DAYPILOT_PREFER_DOTENV"] == "1"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_start_script_fails_fast_without_deepseek_key() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / ".env").write_text("DAYPILOT_LLM_MODE=deepseek\nDEEPSEEK_API_KEY=\n", encoding="utf-8")

        try:
            validate_deepseek_key(root, env={})
        except StartupError as exc:
            assert "DEEPSEEK_API_KEY is missing" in str(exc)
        else:
            raise AssertionError("startup should fail when DEEPSEEK_API_KEY is missing")


def test_start_script_allows_mock_without_deepseek_key() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / ".env").write_text("DAYPILOT_LLM_MODE=mock\nDEEPSEEK_API_KEY=\n", encoding="utf-8")

        validate_deepseek_key(root, env={})


def test_start_script_initializes_database_when_missing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "data" / "db" / "daypilot.sqlite3"

        initialize_runtime_database(db_path)

        connection = sqlite3.connect(db_path)
        try:
            table_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                    'user_profile',
                    'projects',
                    'daily_goals',
                    'goal_versions',
                    'daily_checkins',
                    'project_lifecycle_events',
                    'feedback_messages',
                    'profile_memory_events',
                    'soul_sync_retry_jobs',
                    'ability_state',
                    'weekly_reports',
                    'weekly_focus',
                    'career_chat_sessions',
                    'career_chat_messages',
                    'career_profile_update_suggestions',
                    'career_chat_memory_summaries',
                    'career_recommendation_actions'
                  )
                """
            ).fetchone()[0]
        finally:
            connection.close()
        assert table_count == 17


def test_start_script_ignores_external_schema_path_when_preparing_runtime() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "init_db.sql", root / "scripts" / "init_db.sql")
        (root / ".env").write_text("DAYPILOT_LLM_MODE=mock\nDEEPSEEK_API_KEY=\n", encoding="utf-8")

        backup_path = prepare_runtime(
            root,
            env={
                "DAYPILOT_LLM_MODE": "mock",
                "DAYPILOT_SCHEMA_PATH": str(root / "missing" / "bad.sql"),
                "DAYPILOT_DATA_DIR": str(root / "wrong-data-dir"),
            },
        )

        assert backup_path is None
        assert (root / "data" / "db" / "daypilot.sqlite3").exists()
        assert not (root / "wrong-data-dir").exists()


def test_start_script_backs_up_existing_database_before_runtime_preparation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "init_db.sql", root / "scripts" / "init_db.sql")
        paths = runtime_paths(root)
        paths.db_path.parent.mkdir(parents=True)
        connection = sqlite3.connect(paths.db_path)
        try:
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('before')")
            connection.commit()
        finally:
            connection.close()
        (root / ".env").write_text(
            "DAYPILOT_LLM_MODE=deepseek\nDEEPSEEK_API_KEY=test-key\n",
            encoding="utf-8",
        )

        backup_path = prepare_runtime(root, env={})

        assert backup_path is not None
        connection = sqlite3.connect(backup_path)
        try:
            value = connection.execute("SELECT value FROM marker").fetchone()[0]
        finally:
            connection.close()
        assert value == "before"


def test_stop_script_removes_stale_pid_files_under_data_tmp() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        pid_file = Path(temp_dir) / "data" / "tmp" / "backend.pid"
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text("999999999", encoding="ascii")

        result = stop_daypilot_processes(pid_files=(pid_file,), ports=(), wait_for_ports=False)

        assert result.stopped_count == 0
        assert not pid_file.exists()


def test_stop_script_uses_default_pid_files_and_ports() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        state_dir = root / "data" / "tmp"
        state_dir.mkdir(parents=True)
        (state_dir / "backend.pid").write_text("999999999", encoding="ascii")

        assert stop_daypilot(root, ports=()) == 0
        assert not (state_dir / "backend.pid").exists()


def test_restore_script_restores_latest_backup_and_protects_current_db() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "data" / "db" / "daypilot.sqlite3"
        backup_dir = root / "data" / "backups"
        db_path.parent.mkdir(parents=True)
        backup_dir.mkdir(parents=True)
        db_path.write_text("current", encoding="utf-8")
        old_backup = backup_dir / "daypilot_20260101_090000.sqlite3"
        new_backup = backup_dir / "daypilot_20260102_090000.sqlite3"
        ignored_backup = backup_dir / "daypilot_before_restore_20260103_090000.sqlite3"
        old_backup.write_text("old", encoding="utf-8")
        new_backup.write_text("new", encoding="utf-8")
        ignored_backup.write_text("ignored", encoding="utf-8")

        assert latest_backup(backup_dir) == new_backup

        result = restore_database(db_path=db_path, backup_dir=backup_dir)

        assert result["restored_from"] == new_backup
        assert result["before_restore_backup"] is not None
        assert result["before_restore_backup"].read_text(encoding="utf-8") == "current"
        assert db_path.read_text(encoding="utf-8") == "new"


def main() -> None:
    test_start_stop_scripts_are_portable_and_keep_state_under_data_tmp()
    test_start_script_pins_development_environment_to_repo_paths()
    test_daypilot_process_identity_covers_current_and_packaged_entrypoints()
    test_package_launcher_prepares_user_runtime_and_env_paths()
    test_start_script_fails_fast_without_deepseek_key()
    test_start_script_allows_mock_without_deepseek_key()
    test_start_script_initializes_database_when_missing()
    test_start_script_ignores_external_schema_path_when_preparing_runtime()
    test_start_script_backs_up_existing_database_before_runtime_preparation()
    test_stop_script_removes_stale_pid_files_under_data_tmp()
    test_stop_script_uses_default_pid_files_and_ports()
    test_restore_script_restores_latest_backup_and_protects_current_db()
    print("PASS: runtime scripts are portable, initialize safely, and keep state under data/tmp")


if __name__ == "__main__":
    main()
