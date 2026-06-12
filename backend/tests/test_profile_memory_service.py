from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config.settings import DayPilotSettings  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.profile_memory_service import apply_profile_memory_from_feedback  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _settings(*, mode: str = "mock", key: str | None = "test-key") -> DayPilotSettings:
    return DayPilotSettings(
        llm_mode=mode,
        deepseek_api_key=key,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=3,
        deepseek_max_tokens=300,
        deepseek_thinking="disabled",
    )


def _deepseek_payload(content: str) -> dict[str, Any]:
    return {
        "id": "profile-memory-response",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 长期方向",
                "",
                "长期方向原文。",
                "",
                "## 当前项目",
                "",
                "旧项目段落必须保留。",
                "",
                "## 用户偏好",
                "",
                "用户更喜欢：",
                "",
                "- 小而可交付的目标。",
                "",
                "## 避免事项",
                "",
                "生成目标时要避免：",
                "",
                "- 不要把长期愿望压成一天任务。",
                "",
                "## 时间预算与目标数量",
                "- 用户每天有效工作时间约为 4 小时。",
                "",
                "## 每日目标原则",
                "",
                "每日目标原则原文。",
                "",
                "## 反馈修正规则",
                "",
                "反馈修正规则原文。",
                "",
                "## 周报原则",
                "",
                "周报原则原文。",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _seed_feedback(db_path: Path, raw_message: str) -> int:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a flexible personal work system.",
                goal_preferences={
                    "stable_preferences": ["小而可交付的目标。"],
                    "avoid_patterns": ["不要把长期愿望压成一天任务。"],
                    "time_scope_rules": ["用户每天有效工作时间约为 4 小时。"],
                },
                avoid_patterns=["不要把长期愿望压成一天任务。"],
            )
            goal_id = repo.create_daily_goal(
                connection,
                goal_date="2026-06-08",
                context_snapshot={"source": "profile-memory-test"},
                generated_at="2026-06-08 09:00:00",
            )
            version_id = repo.create_goal_version(
                connection,
                daily_goal_id=goal_id,
                version_no=1,
                is_active=1,
                main_goal="整理一个可交付的小目标。",
                goal_reason="Seeded test goal.",
                success_criteria=["留下笔记"],
                estimated_minutes=60,
                difficulty_level=2,
                minimum_version="有一份笔记。",
                goal_type="writing",
                revision_source="initial_generation",
            )
            return repo.create_feedback_message(
                connection,
                daily_goal_id=goal_id,
                before_version_id=version_id,
                after_version_id=version_id,
                raw_message=raw_message,
                feedback_type="quality_issue",
                affected_scope="today",
                interpretation_json={"raw_feedback": raw_message},
                extracted_constraints={},
                extracted_preferences={},
                memory_action="none",
                should_regenerate_goal=1,
                is_resolved=1,
            )
    finally:
        connection.close()


def test_deepseek_profile_memory_updates_db_soul_and_event_without_confidence_gate() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "profile-memory.sqlite3"
        soul_path = _soul_file(root)
        before_soul = soul_path.read_text(encoding="utf-8")
        feedback_id = _seed_feedback(
            db_path,
            "以后不要给我太抽象的目标，我更喜欢能留下文件、代码或笔记的目标。",
        )
        output = {
            "preference_items": ["能留下文件、代码、笔记或决策记录的目标。"],
            "avoid_items": ["不要给太抽象的目标。"],
            "time_scope_rules": ["时间不足时优先缩小范围，保留可交付结果。"],
            "ignored_items": [],
            "reason": "用户表达了稳定目标偏好和避免事项。",
            "confidence": 0.05,
        }
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(json.dumps(output, ensure_ascii=False))
            )
            result = apply_profile_memory_from_feedback(
                db_path,
                feedback_id,
                {"raw_feedback": "seed"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "applied"
        assert result["applied_items_count"] == 3
        assert result["soul_synced"] is False
        assert result["soul_sync_queued"] is False
        assert result["soul_sync_disabled_reason"] == "profile_memory_no_longer_writes_soul"

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            events = repo.list_recent_profile_memory_events(connection, limit=5)
        finally:
            connection.close()

        preferences = profile["goal_preferences"]
        assert "能留下文件、代码、笔记或决策记录的目标。" in preferences["stable_preferences"]
        assert "不要给太抽象的目标。" in preferences["avoid_patterns"]
        assert "时间不足时优先缩小范围，保留可交付结果。" in preferences["time_scope_rules"]
        assert "不要给太抽象的目标。" in profile["avoid_patterns"]
        assert events[0]["applied"] == 1
        assert events[0]["confidence"] == 0.05

        assert soul_path.read_text(encoding="utf-8") == before_soul


def test_one_time_time_limit_is_skipped_and_not_written_to_soul() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "profile-memory-skip.sqlite3"
        soul_path = _soul_file(root)
        before = soul_path.read_text(encoding="utf-8")
        feedback_id = _seed_feedback(db_path, "今天只有 30 分钟，请缩小范围。")

        result = apply_profile_memory_from_feedback(
            db_path,
            feedback_id,
            {"raw_feedback": "今天只有 30 分钟"},
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "skipped"
        assert result["applied_items_count"] == 0
        assert result["soul_synced"] is False
        assert soul_path.read_text(encoding="utf-8") == before

        connection = initialize_database(db_path)
        try:
            events = repo.list_recent_profile_memory_events(connection, limit=5)
        finally:
            connection.close()
        assert len(events) == 1
        assert events[0]["applied"] == 0
        assert "今天只有 30 分钟" in events[0]["ignored_items"][0]


def test_mock_fallback_applies_explicit_stable_phrases() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "profile-memory-fallback.sqlite3"
        soul_path = _soul_file(root)
        before_soul = soul_path.read_text(encoding="utf-8")
        feedback_id = _seed_feedback(
            db_path,
            "以后不要给我太抽象的目标。我更喜欢能留下文件或代码的目标。",
        )

        result = apply_profile_memory_from_feedback(
            db_path,
            feedback_id,
            {"raw_feedback": "explicit"},
            settings=_settings(mode="mock"),
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["applied_items_count"] == 2
        assert result["soul_synced"] is False
        assert result["soul_sync_queued"] is False
        assert soul_path.read_text(encoding="utf-8") == before_soul

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()
        assert "能留下文件或代码的目标" in profile["goal_preferences"]["stable_preferences"]
        assert "不要给我太抽象的目标" in profile["goal_preferences"]["avoid_patterns"]


def test_invalid_deepseek_output_and_unparseable_fallback_returns_failed_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "profile-memory-failed.sqlite3"
        soul_path = _soul_file(root)
        before = soul_path.read_text(encoding="utf-8")
        feedback_id = _seed_feedback(db_path, "随便改一下这个目标。")
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload('{"bad": true}')
            )
            result = apply_profile_memory_from_feedback(
                db_path,
                feedback_id,
                {"raw_feedback": "随便改一下"},
                settings=_settings(mode="deepseek"),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "failed"
        assert soul_path.read_text(encoding="utf-8") == before

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            events = repo.list_recent_profile_memory_events(connection, limit=5)
        finally:
            connection.close()

        assert profile["goal_preferences"]["stable_preferences"] == ["小而可交付的目标。"]
        assert events == []


def main() -> None:
    test_deepseek_profile_memory_updates_db_soul_and_event_without_confidence_gate()
    test_one_time_time_limit_is_skipped_and_not_written_to_soul()
    test_mock_fallback_applies_explicit_stable_phrases()
    test_invalid_deepseek_output_and_unparseable_fallback_returns_failed_without_mutation()
    print("PASS: profile memory extraction, fallback, disabled SOUL writes, and failure handling verified")


if __name__ == "__main__":
    main()
