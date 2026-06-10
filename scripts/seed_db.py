from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repositories.database import DEFAULT_DB_PATH, initialize_database  # noqa: E402
from backend.repositories.seed import DEFAULT_SEED_PATH, seed_example_workweek  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the DayPilot SQLite database with example workweek data.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path. Defaults to data/db/daypilot.sqlite3.",
    )
    parser.add_argument(
        "--seed-path",
        default=str(DEFAULT_SEED_PATH),
        help="Seed JSON path. Defaults to data/seed/example_workweek.json.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the target database file before initializing and seeding it.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if args.reset and db_path.exists():
        db_path.unlink()

    connection = initialize_database(db_path)
    try:
        counts = seed_example_workweek(connection, args.seed_path)
    finally:
        connection.close()

    print(f"Seeded DayPilot database at {db_path}")
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
