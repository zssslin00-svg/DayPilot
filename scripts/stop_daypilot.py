from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data" / "tmp"
PID_FILES = (STATE_DIR / "backend.pid", STATE_DIR / "frontend.pid")


def stop_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def stop_from_pid_file(path: str | Path) -> bool:
    pid_file = Path(path)
    try:
        raw_pid = pid_file.read_text(encoding="ascii").strip()
        pid = int(raw_pid)
    except (FileNotFoundError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False

    stopped = stop_pid(pid)
    pid_file.unlink(missing_ok=True)
    return stopped


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stopped_count = sum(1 for path in PID_FILES if stop_from_pid_file(path))
    print(f"DayPilot stop command completed. Stopped processes: {stopped_count}")


if __name__ == "__main__":
    main()
