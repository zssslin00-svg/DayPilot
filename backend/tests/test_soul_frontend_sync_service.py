from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.services import soul_frontend_sync_service as sync_service  # noqa: E402


def _soul_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                "当前 active 项目有 1 个。",
                "",
                "1. Alpha 项目：当前进度：旧进度。项目今日目标：旧目标。",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付。",
            ]
        ),
        encoding="utf-8",
    )


def test_frontend_activity_updates_existing_project_and_recent_records() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        soul_path = Path(temp_dir) / "SOUL.md"
        _soul_file(soul_path)

        result = sync_service.record_frontend_activity_to_soul(
            soul_path=soul_path,
            record_date=date(2026, 6, 12),
            record_type="manual",
            summary="完成了一条可复查记录。",
        )

        assert result["status"] == "synced"
        assert result["soul_sync_queued"] is False
        text = soul_path.read_text(encoding="utf-8")
        assert "## 最近记录" in text
        assert "[manual] 完成了一条可复查记录" in text

        update = sync_service._sync_frontend_activity_to_soul(
            soul_path=soul_path,
            project_name="Alpha 项目",
            progress="新进度",
            today_goal="新目标",
            record_date=date(2026, 6, 12),
            record_type="check-in",
            record_summary="Alpha 项目：新进度",
            summary_metadata={"method": "deterministic"},
        )
        assert update["status"] == "synced"
        text = soul_path.read_text(encoding="utf-8")
        assert "当前进度：新进度" in text
        assert "项目今日目标：新目标" in text
        assert "[check-in] Alpha 项目：新进度" in text


def test_recent_records_are_limited_to_window_and_count() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        soul_path = Path(temp_dir) / "SOUL.md"
        _soul_file(soul_path)
        today = date(2026, 6, 20)

        for offset in reversed(range(25)):
            sync_service.record_frontend_activity_to_soul(
                soul_path=soul_path,
                record_date=today - timedelta(days=offset),
                record_type="note",
                summary=f"记录 {offset}",
            )

        records = [
            line
            for line in soul_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ") and "[note]" in line
        ]
        assert len(records) == 15
        assert all("2026-06-05" <= line[2:12] <= "2026-06-20" for line in records)


def test_long_summary_falls_back_to_truncation_when_llm_fails() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        soul_path = Path(temp_dir) / "SOUL.md"
        _soul_file(soul_path)
        original = sync_service.generate_json_with_fallback

        def fail_generation(**_kwargs):
            raise RuntimeError("llm unavailable")

        sync_service.generate_json_with_fallback = fail_generation
        try:
            result = sync_service.record_frontend_activity_to_soul(
                soul_path=soul_path,
                record_date=date(2026, 6, 12),
                record_type="long",
                summary="很长的记录" * 80,
            )
        finally:
            sync_service.generate_json_with_fallback = original

        assert result["status"] == "synced"
        assert result["summary_method"] == "truncated_after_llm_failure"
        assert "[long]" in soul_path.read_text(encoding="utf-8")


def test_appending_current_project_counts_only_project_lines_before_rules() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        soul_path = Path(temp_dir) / "SOUL.md"
        soul_path.write_text(
            "\n".join(
                [
                    "# DayPilot SOUL",
                    "",
                    "## 当前项目",
                    "",
                    "1. Alpha 项目；当前进度：A。",
                    "2. Beta 项目；当前进度：B。",
                    "",
                    "每日生成规则：",
                    "",
                    "- 每个 active 项目都生成一个今日目标。",
                    "- 昨日未完成项目继续承接。",
                    "",
                    "## 用户偏好",
                    "",
                    "- 小目标。",
                ]
            ),
            encoding="utf-8",
        )

        result = sync_service.append_current_project_to_soul(
            soul_path=soul_path,
            project_name="Gamma 项目",
            progress="G",
            target_goal="T",
        )

        assert result["status"] == "synced"
        text = soul_path.read_text(encoding="utf-8")
        assert "3. Gamma 项目" in text
        assert "5. Gamma 项目" not in text


def main() -> None:
    test_frontend_activity_updates_existing_project_and_recent_records()
    test_recent_records_are_limited_to_window_and_count()
    test_long_summary_falls_back_to_truncation_when_llm_fails()
    test_appending_current_project_counts_only_project_lines_before_rules()
    print("PASS: SOUL frontend sync service updates progress and recent records safely")


if __name__ == "__main__":
    main()
