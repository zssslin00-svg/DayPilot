from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "db" / "daypilot.sqlite3"
BACKUP_DIR = ROOT / "data" / "backups"
BACKUP_NAME_PATTERN = re.compile(r"^daypilot_(\d{8})_(\d{6})\.sqlite3$")


def latest_backup(backup_dir: str | Path = BACKUP_DIR) -> Path | None:
    directory = Path(backup_dir)
    if not directory.exists():
        return None
    candidates = [
        path
        for path in directory.glob("daypilot_*.sqlite3")
        if BACKUP_NAME_PATTERN.match(path.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=_backup_timestamp_key)


def _backup_timestamp_key(path: Path) -> tuple[str, str]:
    match = BACKUP_NAME_PATTERN.match(path.name)
    if match is None:
        return ("", "")
    return (match.group(1), match.group(2))


def restore_database(
    *,
    db_path: str | Path = DB_PATH,
    backup_dir: str | Path = BACKUP_DIR,
    source: str | Path | None = None,
) -> dict[str, Path | None]:
    database_path = Path(db_path)
    backups_path = Path(backup_dir)
    restore_source = Path(source) if source is not None else latest_backup(backups_path)
    if restore_source is None or not restore_source.exists():
        raise FileNotFoundError("No DayPilot backup was found under data/backups.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    backups_path.mkdir(parents=True, exist_ok=True)

    before_restore: Path | None = None
    if database_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        before_restore = backups_path / f"daypilot_before_restore_{stamp}.sqlite3"
        shutil.copy2(database_path, before_restore)

    shutil.copy2(restore_source, database_path)
    return {"restored_from": restore_source, "before_restore_backup": before_restore}


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore DayPilot SQLite database from backup.")
    parser.add_argument(
        "source",
        nargs="?",
        help="Optional explicit .sqlite3 backup path. Defaults to latest data/backups/daypilot_*.sqlite3.",
    )
    args = parser.parse_args()

    try:
        result = restore_database(source=args.source)
    except FileNotFoundError as exc:
        print(str(exc))
        raise SystemExit(2) from exc

    before = result["before_restore_backup"]
    if before is not None:
        print(f"Backed up current database to {before}")
    print(f"Restored database from {result['restored_from']}")


if __name__ == "__main__":
    main()
