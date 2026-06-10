from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.soul_context import load_soul_context  # noqa: E402


TEXT_HYGIENE_PATHS = [
    ROOT / "SOUL.md",
    ROOT / "prompts" / "goal_generation" / "system_prompt.md",
    ROOT / "prompts" / "goal_generation" / "user_prompt_template.md",
    ROOT / "backend" / "schemas" / "daily_goal.schema.json",
    ROOT / "backend" / "api" / "server.py",
    ROOT / "backend" / "services" / "today_goal_service.py",
    ROOT / "backend" / "services" / "goal_feedback_service.py",
    ROOT / "backend" / "services" / "profile_memory_service.py",
    ROOT / "backend" / "services" / "project_progress_service.py",
    ROOT / "backend" / "services" / "project_lifecycle_service.py",
    ROOT / "backend" / "services" / "weekly_report_service.py",
    ROOT / "backend" / "services" / "goal_critic.py",
    ROOT / "backend" / "services" / "llm_logging.py",
    ROOT / "frontend" / "pages" / "index.html",
    ROOT / "frontend" / "services" / "today-goal.js",
    ROOT / "frontend" / "styles" / "main.css",
]

MOJIBAKE_MARKERS = [
    "???",
    "锛",
    "鐨",
    "浠婂",
    "浠ｇ",
    "鈥",
    "闈炲",
    "蹇呴",
    "璇锋",
    "鍛ㄤ",
    "瀹屾",
]


def test_soul_context_loads_utf8_file() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "SOUL.md"
        path.write_text("DayPilot 长期上下文", encoding="utf-8")

        soul = load_soul_context(path)

        assert soul.loaded is True
        assert soul.content == "DayPilot 长期上下文"


def test_soul_context_handles_missing_file() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "missing.md"

        soul = load_soul_context(path)

        assert soul.loaded is False
        assert soul.content == ""


def test_core_text_files_do_not_contain_known_mojibake_markers() -> None:
    offenders: list[str] = []
    for path in TEXT_HYGIENE_PATHS:
        text = path.read_text(encoding="utf-8")
        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {marker}")

    assert offenders == []


def main() -> None:
    test_soul_context_loads_utf8_file()
    test_soul_context_handles_missing_file()
    test_core_text_files_do_not_contain_known_mojibake_markers()
    print("PASS: SOUL.md loading and core text hygiene verified")


if __name__ == "__main__":
    main()
