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
from backend.services.soul_sync_service import get_soul_sync_status, retry_soul_sync_jobs  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _settings() -> DayPilotSettings:
    return DayPilotSettings(
        llm_mode="deepseek",
        deepseek_api_key="test-key",
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


def _valid_soul_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 长期方向",
                "",
                "Long-term direction.",
                "",
                "## 当前项目",
                "",
                "Project section.",
                "",
                "## 用户偏好",
                "",
                "用户更喜欢：",
                "",
                "- Existing preference.",
                "",
                "## 避免事项",
                "",
                "生成目标时要避免：",
                "",
                "- Existing avoid item.",
                "",
                "## 时间预算与目标数量",
                "- Existing time rule.",
                "",
                "## 每日目标原则",
                "",
                "Daily goal principles.",
            ]
        ),
        encoding="utf-8",
    )


def _seed_feedback(db_path: Path, raw_message: str) -> int:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily goal loop.",
                goal_preferences={
                    "stable_preferences": ["Existing preference."],
                    "avoid_patterns": ["Existing avoid item."],
                    "time_scope_rules": ["Existing time rule."],
                },
                avoid_patterns=["Existing avoid item."],
            )
            goal_id = repo.create_daily_goal(
                connection,
                goal_date="2026-06-08",
                context_snapshot={"source": "soul-retry-test"},
                generated_at="2026-06-08 09:00:00",
            )
            version_id = repo.create_goal_version(
                connection,
                daily_goal_id=goal_id,
                version_no=1,
                is_active=1,
                main_goal="Create a small deliverable.",
                goal_reason="Seeded test goal.",
                success_criteria=["One note exists"],
                estimated_minutes=60,
                difficulty_level=2,
                minimum_version="One note.",
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


def test_profile_memory_soul_failure_is_queued_and_retry_succeeds() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "soul-retry.sqlite3"
        soul_path = root / "SOUL.md"
        soul_path.write_text("# DayPilot SOUL\n\n## Broken\n", encoding="utf-8")
        feedback_id = _seed_feedback(db_path, "Please remember that concrete artifacts matter.")
        output = {
            "preference_items": ["Prefer goals with concrete artifacts."],
            "avoid_items": ["Avoid abstract-only goals."],
            "time_scope_rules": ["Keep daily scope small when time is limited."],
            "ignored_items": [],
            "reason": "Stable planning preference.",
            "confidence": 0.9,
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
                settings=_settings(),
                soul_path=soul_path,
            ).payload
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result["status"] == "applied"
        assert result["soul_synced"] is False
        assert result["soul_sync_queued"] is True

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            jobs = repo.list_soul_sync_retry_jobs(connection)
        finally:
            connection.close()

        assert "Prefer goals with concrete artifacts." in profile["goal_preferences"]["stable_preferences"]
        assert len(jobs) == 1
        assert jobs[0]["job_type"] == "profile_memory"
        assert jobs[0]["status"] == "pending"

        _valid_soul_file(soul_path)
        retry_payload = retry_soul_sync_jobs(db_path, soul_path=soul_path).payload
        assert retry_payload["retried"] == 1
        assert retry_payload["results"][0]["status"] == "succeeded"
        assert get_soul_sync_status(db_path)["counts"]["succeeded"] == 1
        assert "Prefer goals with concrete artifacts." in soul_path.read_text(encoding="utf-8")


def main() -> None:
    test_profile_memory_soul_failure_is_queued_and_retry_succeeds()
    print("PASS: SOUL sync failures are queued and can be retried")


if __name__ == "__main__":
    main()
