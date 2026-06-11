from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daypilot_processes import (  # noqa: E402
    DEFAULT_DAYPILOT_PORTS,
    DayPilotProcessError,
    stop_daypilot_processes,
)

STATE_DIR = ROOT / "data" / "tmp"
PID_FILES = (STATE_DIR / "backend.pid", STATE_DIR / "frontend.pid")


def stop_daypilot(
    root: str | Path = ROOT,
    *,
    ports: tuple[int, ...] = DEFAULT_DAYPILOT_PORTS,
) -> int:
    state_dir = Path(root) / "data" / "tmp"
    result = stop_daypilot_processes(
        pid_files=(state_dir / "backend.pid", state_dir / "frontend.pid"),
        ports=ports,
        current_pid=os.getpid(),
    )
    return result.stopped_count


def main() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        stopped_count = stop_daypilot(ROOT)
    except DayPilotProcessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"DayPilot stop command completed. Stopped processes: {stopped_count}")


if __name__ == "__main__":
    main()
