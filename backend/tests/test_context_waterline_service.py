from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config.settings import DayPilotSettings  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services import context_waterline_service as waterline_module  # noqa: E402
from backend.services.context_waterline_service import (  # noqa: E402
    prepare_career_chat_context,
)


def _settings(limit: int) -> DayPilotSettings:
    return DayPilotSettings(
        llm_mode="mock",
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=3,
        deepseek_max_tokens=300,
        deepseek_thinking="disabled",
        context_limit_tokens=limit,
    )


def _context(*, session_id: int = 1, chat_history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "today": "2026-06-12",
        "latest_message": "这是最新用户问题，不能被压缩。",
        "available_minutes": 60,
        "session_id": session_id,
        "user_profile": {
            "id": 1,
            "long_term_direction": "长期方向不能被压缩。",
            "goal_preferences": {"stable_preferences": ["小而可交付"]},
            "avoid_patterns": ["不要空泛"],
            "career_profile": {"current_skills": ["Python"]},
        },
        "active_projects": [{"id": 1, "name": "Agent evaluation", "status": "active"}],
        "completed_projects": [],
        "ability_state": {},
        "recent_daily_goals": [],
        "recent_checkins": [],
        "recent_feedback_messages": [],
        "recent_weekly_focus": [],
        "chat_history": chat_history or [],
        "soul_loaded": True,
        "soul_path": "SOUL.md",
        "soul_excerpt": "# DayPilot SOUL",
    }


def test_tier0_leaves_context_unchanged() -> None:
    original = _context(
        chat_history=[
            {"id": 1, "role": "user", "content": "短消息", "recommendations": []},
            {"id": 2, "role": "assistant", "content": "短回复", "recommendations": []},
        ]
    )

    result = prepare_career_chat_context(
        context=original,
        soul_text="# DayPilot SOUL",
        settings=_settings(64_000),
    )

    assert result.metadata["tier"] == "tier_0"
    assert result.context["latest_message"] == original["latest_message"]
    assert result.context["chat_history"] == original["chat_history"]
    assert result.context["user_profile"] == original["user_profile"]


def test_tier1_snips_old_assistant_and_long_recent_fields_without_touching_latest_message() -> None:
    long_assistant = "a" * 1800
    original = _context(
        chat_history=[
            {
                "id": 1,
                "role": "assistant",
                "content": long_assistant,
                "recommendations": [
                    {
                        "title": "Long card",
                        "why_it_fits": "x" * 500,
                        "deliverable": "A note",
                        "project_binding": {"kind": "existing_project", "project_name": "Agent evaluation"},
                    }
                ],
            }
        ]
    )
    original["recent_feedback_messages"] = [{"id": 1, "raw_message": "b" * 900}]

    result = prepare_career_chat_context(
        context=original,
        soul_text="# DayPilot SOUL",
        settings=_settings(1500),
    )

    assert result.metadata["tier"] == "tier_1"
    assert result.context["latest_message"] == original["latest_message"]
    assistant = result.context["chat_history"][0]
    assert "[snipped" in assistant["content"]
    assert "why_it_fits" not in assistant["recommendations"][0]
    assert " [snipped " in result.context["recent_feedback_messages"][0]["raw_message"]


def test_tier2_prunes_old_chat_to_placeholder_and_caps_recent_records() -> None:
    history = [
        {"id": index, "role": "user" if index % 2 else "assistant", "content": "c" * 350, "recommendations": []}
        for index in range(1, 13)
    ]
    original = _context(chat_history=history)
    original["recent_daily_goals"] = [{"id": index, "note": "goal"} for index in range(6)]

    result = prepare_career_chat_context(
        context=original,
        soul_text="# DayPilot SOUL",
        settings=_settings(1500),
    )

    assert result.metadata["tier"] == "tier_2"
    assert result.context["chat_history"][0]["content"].startswith("[Content compacted")
    assert [item["id"] for item in result.context["chat_history"][1:]] == [7, 8, 9, 10, 11, 12]
    assert len(result.context["recent_daily_goals"]) == 3
    assert result.context["omitted_counts"]["recent_daily_goals"] == 3


def test_tier3_generates_persistent_incremental_summary() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "waterline.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                session_id = repo.create_career_chat_session(connection, title="Long career chat")
                for index in range(1, 13):
                    repo.create_career_chat_message(
                        connection,
                        session_id=session_id,
                        role="user" if index % 2 else "assistant",
                        content=f"历史消息 {index} " + ("记忆系统策略 " * 160),
                        recommendations=[],
                        context_snapshot={"source": "test"},
                    )
                history = repo.list_recent_career_chat_messages(connection, session_id, limit=24)
        finally:
            connection.close()

        first = prepare_career_chat_context(
            db_path,
            context=_context(session_id=session_id, chat_history=history),
            soul_text="# DayPilot SOUL",
            settings=_settings(220),
        )

        assert first.metadata["tier"] == "tier_3"
        assert first.metadata["tier3"]["summary_generated"] is True
        assert first.context["conversation_summary"]["schema_version"] == "career_chat_memory_summary.v1"
        assert len(first.context["chat_history"]) == 4

        connection = initialize_database(db_path)
        try:
            summary = repo.get_career_chat_memory_summary_by_session(connection, session_id)
            first_source_count = len(summary["source_message_ids"])
            first_covered = summary["covered_through_message_id"]
            with connection:
                for index in range(13, 19):
                    repo.create_career_chat_message(
                        connection,
                        session_id=session_id,
                        role="user" if index % 2 else "assistant",
                        content=f"新增历史 {index} " + ("增量摘要 " * 160),
                        recommendations=[],
                        context_snapshot={"source": "test"},
                    )
            history = repo.list_recent_career_chat_messages(connection, session_id, limit=24)
        finally:
            connection.close()

        second = prepare_career_chat_context(
            db_path,
            context=_context(session_id=session_id, chat_history=history),
            soul_text="# DayPilot SOUL",
            settings=_settings(220),
        )

        assert second.metadata["tier"] == "tier_3"
        assert second.metadata["tier3"]["summary_generated"] is True
        connection = initialize_database(db_path)
        try:
            updated = repo.get_career_chat_memory_summary_by_session(connection, session_id)
        finally:
            connection.close()
        assert len(updated["source_message_ids"]) > first_source_count
        assert updated["covered_through_message_id"] > first_covered


def test_tier3_reuses_existing_summary_without_llm_when_no_delta() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "waterline-no-delta.sqlite3"
        connection = initialize_database(db_path)
        try:
            with connection:
                session_id = repo.create_career_chat_session(connection, title="Covered session")
                for index in range(1, 9):
                    repo.create_career_chat_message(
                        connection,
                        session_id=session_id,
                        role="user" if index % 2 else "assistant",
                        content=f"已覆盖历史 {index} " + ("不应再次摘要 " * 160),
                        recommendations=[],
                        context_snapshot={"source": "test"},
                    )
                repo.upsert_career_chat_memory_summary(
                    connection,
                    session_id=session_id,
                    summary_payload={
                        "schema_version": "career_chat_memory_summary.v1",
                        "progress": ["旧消息已经摘要完成。"],
                        "files": [],
                        "todo": [],
                        "context": ["保留已有摘要即可。"],
                        "source_message_count": 4,
                    },
                    covered_through_message_id=4,
                    source_message_ids=[1, 2, 3, 4],
                    llm_metadata={"provider": "mock"},
                )
                history = repo.list_recent_career_chat_messages(connection, session_id, limit=24)
        finally:
            connection.close()

        original_generate = waterline_module.generate_json_with_fallback

        def fail_if_called(**_kwargs: object) -> object:
            raise AssertionError("summary LLM should not be called when there is no delta")

        waterline_module.generate_json_with_fallback = fail_if_called
        try:
            result = prepare_career_chat_context(
                db_path,
                context=_context(session_id=session_id, chat_history=history),
                soul_text="# DayPilot SOUL",
                settings=_settings(220),
            )
        finally:
            waterline_module.generate_json_with_fallback = original_generate

        assert result.metadata["tier"] == "tier_3"
        assert result.metadata["tier3"]["summary_generated"] is False
        assert result.metadata["tier3"]["delta_message_count"] == 0
        assert result.context["conversation_summary"]["progress"] == ["旧消息已经摘要完成。"]
        assert [item["id"] for item in result.context["chat_history"]] == [5, 6, 7, 8]


def main() -> None:
    test_tier0_leaves_context_unchanged()
    test_tier1_snips_old_assistant_and_long_recent_fields_without_touching_latest_message()
    test_tier2_prunes_old_chat_to_placeholder_and_caps_recent_records()
    test_tier3_generates_persistent_incremental_summary()
    test_tier3_reuses_existing_summary_without_llm_when_no_delta()
    print("PASS: context waterline service tiers and persistent summaries verified")


if __name__ == "__main__":
    main()
