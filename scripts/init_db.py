from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repositories.database import DEFAULT_DB_PATH, initialize_database  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the DayPilot SQLite database.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path. Defaults to data/db/daypilot.sqlite3.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    connection = initialize_database(db_path)
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
                'weekly_focus'
              )
            """
        ).fetchone()[0]
    finally:
        connection.close()

    print(f"Initialized DayPilot database at {db_path}")
    print(f"Core tables present: {table_count}/12")


if __name__ == "__main__":
    main()
