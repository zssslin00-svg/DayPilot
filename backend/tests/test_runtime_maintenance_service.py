from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.runtime_maintenance_service import cleanup_runtime_data  # noqa: E402


def _touch(path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(path.name, encoding="utf-8")
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def test_cleanup_runtime_data_applies_retention_without_deleting_minimums() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        now = datetime(2026, 6, 9, 12, 0, 0)
        old = now - timedelta(days=40)
        recent = now - timedelta(days=1)

        for index in range(12):
            _touch(root / "data" / "backups" / f"daypilot_old_{index:02d}.sqlite3", old + timedelta(minutes=index))
        for index in range(22):
            _touch(root / "data" / "backups" / f"SOUL_old_{index:02d}.md", old + timedelta(minutes=index))
        _touch(root / "data" / "llm_logs" / "mock" / "2026-05-01.jsonl", old)
        _touch(root / "data" / "llm_logs" / "mock" / "2026-06-09.jsonl", recent)
        _touch(root / "data" / "tmp" / "old.tmp", old)
        _touch(root / "data" / "tmp" / "backend.pid", old)

        result = cleanup_runtime_data(now=now, root=root)

        assert result["deleted_counts"]["database_backups"] == 2
        assert result["deleted_counts"]["soul_backups"] == 2
        assert result["deleted_counts"]["llm_logs"] == 1
        assert result["deleted_counts"]["tmp_files"] == 1
        assert len(list((root / "data" / "backups").glob("*.sqlite3"))) == 10
        assert len(list((root / "data" / "backups").glob("SOUL_*.md"))) == 20
        assert (root / "data" / "llm_logs" / "mock" / "2026-06-09.jsonl").exists()
        assert (root / "data" / "tmp" / "backend.pid").exists()
        assert not (root / "data" / "tmp" / "old.tmp").exists()


def main() -> None:
    test_cleanup_runtime_data_applies_retention_without_deleting_minimums()
    print("PASS: runtime cleanup applies compact retention safely")


if __name__ == "__main__":
    main()
