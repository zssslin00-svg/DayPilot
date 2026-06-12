<p align="center">
  <img src="docs/assets/daypilot-hero-banner.png" alt="DayPilot AI daily workbench banner" width="920">
</p>

<p align="center">
  <img src="docs/assets/daypilot-logo.png" alt="DayPilot AI workbench logo" width="112">
</p>

<h1 align="center">DayPilot</h1>

<p align="center">A private AI daily workbench driven by SOUL.md.</p>

<p align="center">
  <a href="README.md">中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555">
  <img alt="LLM" src="https://img.shields.io/badge/LLM-DeepSeek-0F766E">
</p>

---

## What Is DayPilot

DayPilot is a local-first, single-user AI daily workbench. It does not try to take over your life planning; it compresses your long-term direction, active projects, and real available energy into goals that can be delivered and reviewed today.

Its project-change entry point is `SOUL.md`. Add, rename, update, remove, or complete projects by editing the `## Current Projects` section; the frontend reads state, refreshes goals, records feedback, handles check-ins, generates weekly reviews, and supports career-planning chat.

Real model mode only needs an API key in `.env` as `DEEPSEEK_API_KEY`. Do not put API keys in `SOUL.md`, the README, or any file that will be committed to git.

## Features

**SOUL-driven projects** - `SOUL.md` is the only user-facing project-change entry point; Today imports the latest project state when it opens or refreshes.

**Flexible project parsing** - Use one line per project or natural-language paragraphs; with a DeepSeek key, DayPilot tries LLM parsing first and falls back conservatively.

**Today goals** - Generate small, clear goals for active projects with acceptance criteria, minimum output, time estimate, and explicit non-goals.

**Feedback revision** - Tell Today "I only have 45 minutes" or "this is too large", and DayPilot creates a new goal version.

**Check-in review** - Record completion, felt difficulty, and tomorrow's direction; project progress can be written back to `SOUL.md`.

**Weekly review** - On Friday, DayPilot uses check-in evidence to produce a weekly report and next-week focus.

**Career planning chat** - Discuss spare time, skill growth, portfolio direction, and career constraints using `SOUL.md`, profile state, project history, and recent records.

**Local-first data** - SQLite, logs, backups, and your real `SOUL.md` stay on your machine by default; mock mode works without an API key.

**No P0 ceremony** - The UI, README, and SOUL template do not require P0/P1/P2 labels; old data remains compatible, and priority is only treated as an internal hint when you clearly express it.

## Screenshot

<p align="center">
  <img src="docs/assets/daypilot-screenshot-today.png" alt="DayPilot Today workbench screenshot" width="860">
</p>

## Quick Start

### Windows

Copy the config and personal-context template:

```bat
cd /d D:\path\to\DayPilot
copy .env.example .env
copy SOUL.example.md SOUL.md
notepad .env
notepad SOUL.md
```

For local mock testing:

```bat
set "DAYPILOT_LLM_MODE=mock" && python scripts\start_daypilot.py --restart
```

For real model mode, put this in `.env`:

```text
DAYPILOT_LLM_MODE=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

Then start DayPilot:

```bat
python scripts\start_daypilot.py --restart
```

### macOS / Linux

```bash
cd /path/to/DayPilot
cp .env.example .env
cp SOUL.example.md SOUL.md
nano .env
nano SOUL.md
DAYPILOT_LLM_MODE=mock python3 scripts/start_daypilot.py --restart
```

Startup opens the frontend by default; the backend runs at `http://127.0.0.1:8000`.

## SOUL Format

Use the `## Current Projects` section like this:

```text
1. DayPilot: current progress: polishing the SOUL sync loop. final goal: build a stable personal work-rhythm system. today's goal: verify that refreshing Today reads SOUL and creates an actionable goal.
2. Training project: current progress: draft data plan exists. final goal: complete a real SFT + RL training record. today's goal: add one sample datum and one evaluation rule.
```

Natural-language paragraphs are also allowed. For example: "I am mainly advancing DayPilot and only want to verify SOUL sync today; the training project should just improve its data plan." With a real DeepSeek key, DayPilot tries to parse flexible text. Without a key, or when parsing is unreliable, it refuses destructive sync.

When you remove an active project from `## Current Projects`, refreshing Today marks it completed while preserving history. If there are no active projects, write `No active projects.` or `暂无 active 项目。`.

## Architecture

```text
backend/      API, services, repositories, schemas
frontend/     Today, History, Weekly, and Career Chat pages
prompts/      Goal-generation prompts and examples
evals/        Cases, rubrics, and evaluation scripts
scripts/      Start, stop, backup, restore, and connectivity checks
data/         Local database, backups, temporary files, and LLM logs
docs/assets/  README image assets
```

The data flow is short: `SOUL.md`, SQLite projects, and history records form the context; the service layer calls DeepSeek or deterministic mock adapters; validated results are written to SQLite and synced back to `SOUL.md` when needed; the frontend displays Today, History, Weekly, and Career Chat.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | HTML + CSS + Vanilla JavaScript |
| Backend | Python 3.10+ standard-library HTTP service |
| Agent runtime | DeepSeek OpenAI-compatible Chat Completions API |
| Local data | SQLite |
| Fallback | Deterministic mock adapters |
| Tests | Python test scripts + eval cases/rubrics |

## Platform Support

| Platform | Status |
| --- | --- |
| Windows | Supported with `scripts\start_daypilot.py` |
| macOS | Source-run support with `python3 scripts/start_daypilot.py` |
| Linux | Source-run support with `python3 scripts/start_daypilot.py` |
| Mobile browser | Pages are responsive; the service still needs to run on a computer |

## Development

```bat
python scripts\check_deepseek_connection.py
python -m evals.run_all
python tests\frontend_api_smoke.py
python scripts\stop_daypilot.py
```

## Data Sync

- `GET /api/projects` remains as a read-only state source.
- `POST /api/projects/lifecycle` is disabled for user writes and returns `410 project_lifecycle_disabled`.
- Today refresh calls SOUL import and syncs the current-project section into SQLite.
- Check-in project progress can be written back to `SOUL.md`, but removed active projects are not re-added.
- Never commit your real `SOUL.md`, `.env`, databases, backups, or LLM logs.

## License

[Apache License 2.0](LICENSE)

## Links

- [中文 README](README.md)
- [SOUL.example.md](SOUL.example.md)
- [Packaging](docs/packaging.md)
