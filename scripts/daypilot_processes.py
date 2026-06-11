from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DAYPILOT_PORTS = (8000, 5173)


class DayPilotProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class StopDayPilotResult:
    stopped_count: int
    stopped_pids: tuple[int, ...]
    skipped_pids: tuple[int, ...]


def stop_daypilot_processes(
    *,
    pid_files: tuple[Path, ...] = (),
    ports: tuple[int, ...] = DEFAULT_DAYPILOT_PORTS,
    current_pid: int | None = None,
    wait_for_ports: bool = True,
) -> StopDayPilotResult:
    pid_file_pids = read_pid_file_pids(pid_files)
    port_pids = set(listening_pids_for_ports(ports))
    own_pid = os.getpid() if current_pid is None else current_pid
    candidate_pids = (pid_file_pids | port_pids) - {own_pid}
    stopped: list[int] = []
    skipped: list[int] = []

    for pid in sorted(candidate_pids):
        command_line = command_line_for_pid(pid)
        if not looks_like_daypilot_process(command_line):
            skipped.append(pid)
            continue
        if stop_pid(pid):
            stopped.append(pid)
        else:
            skipped.append(pid)

    for pid_file in pid_files:
        Path(pid_file).unlink(missing_ok=True)

    if wait_for_ports:
        wait_for_ports_available(ports)

    return StopDayPilotResult(
        stopped_count=len(stopped),
        stopped_pids=tuple(stopped),
        skipped_pids=tuple(skipped),
    )


def read_pid_file_pids(pid_files: tuple[Path, ...]) -> set[int]:
    pids: set[int] = set()
    for path in pid_files:
        pid_file = Path(path)
        try:
            pids.add(int(pid_file.read_text(encoding="ascii").strip()))
        except (FileNotFoundError, ValueError):
            pid_file.unlink(missing_ok=True)
    return pids


def listening_pids_for_ports(ports: tuple[int, ...]) -> list[int]:
    if not ports:
        return []
    if os.name == "nt":
        return _windows_listening_pids_for_ports(ports)
    return _posix_listening_pids_for_ports(ports)


def _windows_listening_pids_for_ports(ports: tuple[int, ...]) -> list[int]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    pids: list[int] = []
    wanted = {f":{port}" for port in ports}
    for raw_line in result.stdout.splitlines():
        parts = raw_line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[-2].upper()
        if state != "LISTENING" or not any(local_address.endswith(suffix) for suffix in wanted):
            continue
        try:
            pids.append(int(parts[-1]))
        except ValueError:
            continue
    return pids


def _posix_listening_pids_for_ports(ports: tuple[int, ...]) -> list[int]:
    pids: list[int] = []
    for port in ports:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        for raw_pid in result.stdout.splitlines():
            try:
                pids.append(int(raw_pid.strip()))
            except ValueError:
                continue
    return pids


def command_line_for_pid(pid: int) -> str:
    if os.name == "nt":
        return _windows_command_line_for_pid(pid)
    return _posix_command_line_for_pid(pid)


def _windows_command_line_for_pid(pid: int) -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    return result.stdout.strip()


def _posix_command_line_for_pid(pid: int) -> str:
    proc_path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc_path.read_bytes()
    except OSError:
        raw = b""
    if raw:
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()

    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    return result.stdout.strip()


def looks_like_daypilot_process(command_line: str) -> bool:
    normalized = command_line.replace("\\", "/").lower()
    if "backend/api/server.py" in normalized:
        return True
    if "scripts/serve_frontend.py" in normalized:
        return True
    if "scripts/package_launcher.py" in normalized:
        return True
    if "daypilot.exe" in normalized:
        return True
    return "http.server" in normalized and "5173" in normalized and "frontend" in normalized


def stop_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def wait_for_ports_available(ports: tuple[int, ...], timeout_seconds: float = 8.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        remaining = listening_pids_for_ports(ports)
        if not remaining:
            return
        time.sleep(0.25)
    remaining = sorted(set(listening_pids_for_ports(ports)))
    raise DayPilotProcessError(f"Ports 8000/5173 are still in use by process ids: {remaining}")
