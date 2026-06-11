from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "package_launcher.py"
DIST_DIR = ROOT / "exports" / "packages"
WORK_DIR = ROOT / "data" / "tmp" / "pyinstaller-build"
SPEC_DIR = ROOT / "data" / "tmp" / "pyinstaller-spec"

DATA_ITEMS = (
    ("backend/schemas", "backend/schemas"),
    ("frontend", "frontend"),
    ("prompts", "prompts"),
    ("scripts/init_db.sql", "scripts"),
    ("SOUL.example.md", "."),
    (".env.example", "."),
    ("data/seed", "data/seed"),
    ("data/config", "data/config"),
)


class BuildPackageError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a DayPilot desktop package with PyInstaller.")
    parser.add_argument(
        "--target",
        choices=("current", "windows", "macos", "linux"),
        default="current",
        help="Target platform. Cross-compilation is not supported; the target must match this OS.",
    )
    parser.add_argument("--name", default="DayPilot", help="Executable/package name.")
    parser.add_argument("--no-zip", action="store_true", help="Do not create a zip archive after building.")
    parser.add_argument(
        "--mac-command",
        action="store_true",
        help="On macOS, create a double-clickable .command launcher beside the package folder.",
    )
    return parser.parse_args(argv)


def current_target() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def ensure_target_matches(target: str) -> str:
    resolved = current_target() if target == "current" else target
    current = current_target()
    if resolved != current:
        raise BuildPackageError(
            f"Cannot build {resolved} package on {current}. Run this script on the target OS."
        )
    return resolved


def ensure_pyinstaller_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildPackageError(
            "PyInstaller is not installed. Install it with: python -m pip install pyinstaller"
        )


def add_data_args() -> list[str]:
    separator = ";" if os.name == "nt" else ":"
    args: list[str] = []
    for source, target in DATA_ITEMS:
        source_path = ROOT / source
        if not source_path.exists():
            continue
        args.extend(["--add-data", f"{source_path}{separator}{target}"])
    return args


def build_command(name: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        name,
        "--paths",
        str(ROOT),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        *add_data_args(),
        str(ENTRYPOINT),
    ]


def package_path(name: str, target: str) -> Path:
    suffix = ".exe" if target == "windows" else ""
    executable = DIST_DIR / name / f"{name}{suffix}"
    if not executable.exists():
        raise BuildPackageError(f"Expected package executable was not created: {executable}")
    return DIST_DIR / name


def create_archive(package_dir: Path, name: str, target: str) -> Path:
    archive_base = DIST_DIR / f"{name}-{target}"
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", package_dir.parent, package_dir.name))
    return archive_path


def create_macos_command(package_dir: Path, name: str) -> Path:
    command_path = DIST_DIR / f"{name}.command"
    command_path.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                'DIR="$(cd "$(dirname "$0")" && pwd)"',
                f'"$DIR/{package_dir.name}/{name}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_path.chmod(0o755)
    return command_path


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        target = ensure_target_matches(args.target)
        ensure_pyinstaller_available()
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        SPEC_DIR.mkdir(parents=True, exist_ok=True)

        command = build_command(args.name)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            raise BuildPackageError(f"PyInstaller failed with exit code {result.returncode}.")

        package_dir = package_path(args.name, target)
        print(f"Built DayPilot package: {package_dir}")
        if target == "macos" and args.mac_command:
            command_path = create_macos_command(package_dir, args.name)
            print(f"Created macOS launcher: {command_path}")
        if not args.no_zip:
            archive_path = create_archive(package_dir, args.name, target)
            print(f"Created archive: {archive_path}")
    except BuildPackageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
