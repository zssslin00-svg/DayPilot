from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "db" / "daypilot.sqlite3"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "scripts" / "init_db.sql"


def connect_database(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with DayPilot defaults enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(
    db_path: str | Path = DEFAULT_DB_PATH,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> sqlite3.Connection:
    """Create the DayPilot SQLite schema and return an open connection."""
    connection = connect_database(db_path)
    schema = Path(schema_path).read_text(encoding="utf-8")
    with connection:
        connection.executescript(schema)
    _migrate_projects_status_completed(connection)
    _migrate_project_lifecycle_delete_action(connection)
    _migrate_project_scoped_daily_goals(connection)
    _migrate_project_checkins_completion_status(connection)
    return connection


def _migrate_projects_status_completed(connection: sqlite3.Connection) -> None:
    """Rebuild old projects tables whose status CHECK lacks `completed`."""

    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'projects'
        """
    ).fetchone()
    if row is None:
        return
    table_sql = str(row["sql"] or "")
    if "'completed'" in table_sql:
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE projects_new (
                  id INTEGER PRIMARY KEY,
                  name TEXT NOT NULL UNIQUE,
                  priority TEXT NOT NULL DEFAULT 'P2'
                    CHECK (priority IN ('P0', 'P1', 'P2')),
                  role TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'completed', 'archived')),
                  status_summary TEXT NOT NULL DEFAULT '',
                  planning_bias TEXT NOT NULL DEFAULT '',
                  source_payload TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                INSERT INTO projects_new (
                  id,
                  name,
                  priority,
                  role,
                  status,
                  status_summary,
                  planning_bias,
                  source_payload,
                  created_at,
                  updated_at
                )
                SELECT
                  id,
                  name,
                  priority,
                  role,
                  status,
                  status_summary,
                  planning_bias,
                  source_payload,
                  created_at,
                  updated_at
                FROM projects;

                DROP TABLE projects;
                ALTER TABLE projects_new RENAME TO projects;
                CREATE INDEX IF NOT EXISTS idx_projects_priority
                  ON projects(priority, status, id);
                """
            )
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_project_lifecycle_delete_action(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'project_lifecycle_events'
        """
    ).fetchone()
    if row is None:
        return
    table_sql = str(row["sql"] or "")
    if "'delete_project'" in table_sql:
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE project_lifecycle_events_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_date TEXT NOT NULL DEFAULT (date('now')),
                  raw_message TEXT NOT NULL,
                  action TEXT NOT NULL
                    CHECK (action IN ('create_project', 'complete_project', 'update_project', 'delete_project', 'no_change')),
                  project_id INTEGER,
                  project_name TEXT,
                  priority TEXT,
                  previous_status TEXT,
                  new_status TEXT,
                  previous_status_summary TEXT,
                  new_status_summary TEXT,
                  planning_bias TEXT,
                  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
                  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1)),
                  reason TEXT,
                  llm_metadata TEXT NOT NULL DEFAULT '{}',
                  raw_output TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );

                INSERT INTO project_lifecycle_events_new (
                  id,
                  event_date,
                  raw_message,
                  action,
                  project_id,
                  project_name,
                  priority,
                  previous_status,
                  new_status,
                  previous_status_summary,
                  new_status_summary,
                  planning_bias,
                  confidence,
                  applied,
                  reason,
                  llm_metadata,
                  raw_output,
                  created_at
                )
                SELECT
                  id,
                  event_date,
                  raw_message,
                  action,
                  project_id,
                  project_name,
                  priority,
                  previous_status,
                  new_status,
                  previous_status_summary,
                  new_status_summary,
                  planning_bias,
                  confidence,
                  applied,
                  reason,
                  llm_metadata,
                  raw_output,
                  created_at
                FROM project_lifecycle_events;

                DROP TABLE project_lifecycle_events;
                ALTER TABLE project_lifecycle_events_new RENAME TO project_lifecycle_events;
                CREATE INDEX IF NOT EXISTS idx_project_lifecycle_project_date
                  ON project_lifecycle_events(project_id, event_date, created_at);
                """
            )
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_project_scoped_daily_goals(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'daily_goals'
        """
    ).fetchone()
    if row is None:
        return
    table_sql = str(row["sql"] or "")
    columns = _table_columns(connection, "daily_goals")
    needs_rebuild = "project_id" not in columns or "goal_date TEXT NOT NULL UNIQUE" in table_sql
    if not needs_rebuild:
        return

    rows = connection.execute("SELECT * FROM daily_goals ORDER BY id").fetchall()
    project_ids = _all_project_ids(connection)
    default_project_id = _ensure_migration_project(connection)

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            connection.execute("DROP TABLE IF EXISTS daily_goals_new")
            connection.executescript(
                """
                CREATE TABLE daily_goals_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  profile_id INTEGER NOT NULL DEFAULT 1,
                  project_id INTEGER NOT NULL,
                  goal_date TEXT NOT NULL,
                  week_id TEXT NOT NULL,
                  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
                  is_workday INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
                  status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'checked_in', 'skipped', 'archived')),
                  active_version_id INTEGER,
                  context_snapshot TEXT NOT NULL DEFAULT '{}',
                  revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count >= 0),
                  generated_at TEXT,
                  checked_in_at TEXT,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                  FOREIGN KEY (profile_id) REFERENCES user_profile(id),
                  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                  FOREIGN KEY (active_version_id) REFERENCES goal_versions(id)
                    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
                  UNIQUE (goal_date, project_id)
                );
                """
            )
            for item in rows:
                data = dict(item)
                project_id = _project_id_for_migrated_goal(data, project_ids, default_project_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO daily_goals_new (
                      id, profile_id, project_id, goal_date, week_id, weekday, is_workday,
                      status, active_version_id, context_snapshot, revision_count,
                      generated_at, checked_in_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["id"],
                        data.get("profile_id", 1),
                        project_id,
                        data["goal_date"],
                        data["week_id"],
                        data["weekday"],
                        data["is_workday"],
                        data.get("status", "active"),
                        data.get("active_version_id"),
                        data.get("context_snapshot") or "{}",
                        data.get("revision_count", 0),
                        data.get("generated_at"),
                        data.get("checked_in_at"),
                        data.get("created_at"),
                        data.get("updated_at"),
                    ),
                )
            connection.execute("DROP TABLE daily_goals")
            connection.execute("ALTER TABLE daily_goals_new RENAME TO daily_goals")
            _create_daily_goal_indexes(connection)
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_project_checkins_completion_status(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'daily_checkins'
        """
    ).fetchone()
    if row is None:
        return
    table_sql = str(row["sql"] or "")
    columns = _table_columns(connection, "daily_checkins")
    needs_rebuild = "completion_status" not in columns or "checkin_date TEXT NOT NULL UNIQUE" in table_sql
    if not needs_rebuild:
        return

    rows = connection.execute("SELECT * FROM daily_checkins ORDER BY id").fetchall()

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            connection.execute("DROP TABLE IF EXISTS daily_checkins_new")
            connection.executescript(
                """
                CREATE TABLE daily_checkins_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  daily_goal_id INTEGER NOT NULL UNIQUE,
                  checkin_date TEXT NOT NULL,
                  week_id TEXT NOT NULL,
                  is_workday INTEGER NOT NULL DEFAULT 1 CHECK (is_workday IN (0, 1)),
                  completion_status TEXT NOT NULL DEFAULT 'completed'
                    CHECK (completion_status IN ('completed', 'incomplete')),
                  completion_text TEXT NOT NULL,
                  felt_difficulty INTEGER NOT NULL CHECK (felt_difficulty BETWEEN 1 AND 5),
                  tomorrow_direction TEXT NULL,
                  parsed_completion_rate REAL CHECK (
                    parsed_completion_rate IS NULL OR parsed_completion_rate BETWEEN 0 AND 1
                  ),
                  completed_items TEXT NOT NULL DEFAULT '[]',
                  unfinished_items TEXT NOT NULL DEFAULT '[]',
                  blockers TEXT NOT NULL DEFAULT '[]',
                  actual_outputs TEXT NOT NULL DEFAULT '[]',
                  processor_snapshot TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                  FOREIGN KEY (daily_goal_id) REFERENCES daily_goals(id) ON DELETE CASCADE
                );
                """
            )
            for item in rows:
                data = dict(item)
                completion_status = data.get("completion_status") or _completion_status_from_rate(
                    data.get("parsed_completion_rate")
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO daily_checkins_new (
                      id, daily_goal_id, checkin_date, week_id, is_workday,
                      completion_status, completion_text, felt_difficulty,
                      tomorrow_direction, parsed_completion_rate, completed_items,
                      unfinished_items, blockers, actual_outputs, processor_snapshot,
                      created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["id"],
                        data["daily_goal_id"],
                        data["checkin_date"],
                        data["week_id"],
                        data.get("is_workday", 1),
                        completion_status,
                        data["completion_text"],
                        data["felt_difficulty"],
                        data.get("tomorrow_direction"),
                        data.get("parsed_completion_rate"),
                        data.get("completed_items") or "[]",
                        data.get("unfinished_items") or "[]",
                        data.get("blockers") or "[]",
                        data.get("actual_outputs") or "[]",
                        data.get("processor_snapshot") or "{}",
                        data.get("created_at"),
                        data.get("updated_at"),
                    ),
                )
            connection.execute("DROP TABLE daily_checkins")
            connection.execute("ALTER TABLE daily_checkins_new RENAME TO daily_checkins")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_checkins_week ON daily_checkins(week_id, checkin_date)")
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _all_project_ids(connection: sqlite3.Connection) -> set[int]:
    return {int(row["id"]) for row in connection.execute("SELECT id FROM projects").fetchall()}


def _ensure_migration_project(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM projects WHERE status = 'active' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is not None:
        return int(row["id"])
    row = connection.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        return int(row["id"])
    cursor = connection.execute(
        """
        INSERT INTO projects (name, priority, role, status, status_summary, planning_bias, source_payload)
        VALUES ('DayPilot 默认项目', 'P2', 'active', 'active', '', '', '{"source":"schema_migration"}')
        """
    )
    return int(cursor.lastrowid)


def _project_id_for_migrated_goal(data: dict, project_ids: set[int], default_project_id: int) -> int:
    if data.get("project_id") in project_ids:
        return int(data["project_id"])
    snapshot = _decode_json_object(data.get("context_snapshot"))
    for key in ("project_id", "selected_project_id"):
        value = snapshot.get(key)
        if _is_known_project_id(value, project_ids):
            return int(value)
    project_values = snapshot.get("project_ids")
    if isinstance(project_values, list):
        for value in project_values:
            if _is_known_project_id(value, project_ids):
                return int(value)
    return default_project_id


def _is_known_project_id(value: object, project_ids: set[int]) -> bool:
    try:
        return int(value) in project_ids
    except (TypeError, ValueError):
        return False


def _decode_json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _completion_status_from_rate(value: object) -> str:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return "completed"
    return "completed" if rate >= 0.85 else "incomplete"


def _create_daily_goal_indexes(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX IF NOT EXISTS idx_daily_goals_week ON daily_goals(week_id, goal_date)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_daily_goals_status ON daily_goals(status, goal_date)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_daily_goals_project_date ON daily_goals(project_id, goal_date)")
