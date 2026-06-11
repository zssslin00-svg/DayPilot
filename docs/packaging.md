# DayPilot Packaging

DayPilot supports a PyInstaller-based desktop package for Windows, macOS, and Linux. Build on the same OS you want to publish for; PyInstaller does not produce reliable cross-platform binaries from a different OS.

## What Gets Packaged

- A single launcher executable built from `scripts/package_launcher.py`.
- Bundled application resources: backend schemas, frontend static files, prompts, `scripts/init_db.sql`, `.env.example`, and `SOUL.example.md`.
- User data is not stored inside the install folder. The packaged launcher writes runtime data to the OS app-data directory:
  - Windows: `%APPDATA%\DayPilot`
  - macOS: `~/Library/Application Support/DayPilot`
  - Linux: `~/.local/share/daypilot`
- The first packaged launch creates `SOUL.md`, `.env`, SQLite data, backups, temp files, and LLM logs under that user data directory.
- The generated `.env` defaults to `DAYPILOT_LLM_MODE=mock` so the package opens without a DeepSeek key. Edit the user-data `.env` to use DeepSeek.

## Windows EXE

Run on Windows:

```bat
cd /d D:\tools\vibe_coding\xiangmu\DayPilot
C:\Users\lin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pip install pyinstaller
C:\Users\lin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\build_windows.py
```

Output:

```text
exports\packages\DayPilot\DayPilot.exe
exports\packages\DayPilot-windows.zip
```

Share the zip or the whole `exports\packages\DayPilot` folder. The user starts `DayPilot.exe`; it starts local backend/frontend services and opens the browser.

## macOS Package

Run on macOS:

```bash
cd /path/to/DayPilot
python3 -m pip install pyinstaller
python3 scripts/build_macos.py
```

Output:

```text
exports/packages/DayPilot/DayPilot
exports/packages/DayPilot.command
exports/packages/DayPilot-macos.zip
```

For local or private distribution, zip sharing is enough. For public distribution, macOS will require code signing and notarization with an Apple Developer account.

## Linux Package

Run on Linux:

```bash
cd /path/to/DayPilot
python3 -m pip install pyinstaller
python3 scripts/build_package.py --target linux
```

Output:

```text
exports/packages/DayPilot/DayPilot
exports/packages/DayPilot-linux.zip
```

## Notes

- The package is intentionally one-folder, not one-file. DayPilot has frontend files, schemas, prompts, SQLite data, and editable user profile files; one-folder keeps paths predictable.
- Use `scripts/package_launcher.py --data-dir <path>` to test with a temporary user-data directory.
- If ports `8000` or `5173` are already in use, stop the old DayPilot instance first or launch with custom ports:

```bat
DayPilot.exe --backend-port 18000 --frontend-port 15173
```
