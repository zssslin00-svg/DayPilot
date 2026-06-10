from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "db" / "daypilot.sqlite3"
BACKUP_DIR = ROOT / "data" / "backups"


def backup_database() -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"daypilot_{stamp}.sqlite3"
    shutil.copy2(DB_PATH, target)
    return target


def main() -> None:
    target = backup_database()
    if target is None:
        print("No database exists yet; skipped backup.")
    else:
        print(f"Backed up database to {target}")


if __name__ == "__main__":
    main()
