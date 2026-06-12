from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services import career_chat_service as career_chat_module  # noqa: E402
from backend.services.career_chat_service import (  # noqa: E402
    decide_career_profile_suggestion,
    get_career_chat_history,
    send_career_chat_message,
)


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 长期方向",
                "",
                "把自己打造为一个灵活的系统。",
                "",
                "## 当前项目",
                "",
                "当前项目段落。",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付的目标。",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _seed_profile_and_project(db_path: Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build flexible AI Agent and rule-system abilities.",
                current_focus_projects=["Agent evaluation"],
                career_profile={
                    "current_skills": ["Python"],
                    "development_intentions": ["希望发展到 AI Agent 方向"],
                },
                default_available_minutes=90,
            )
            repo.create_project(
                connection,
                name="Agent evaluation",
                priority="P0",
                status="active",
                project_state={
                    "summary": "Preparing an evaluation workflow.",
                    "planning_guidance": "Keep work project-based and verifiable.",
                    "target_goal": "Deliver a small Agent evaluation experiment.",
                    "facts": [],
                    "updated_from": {"source": "test"},
                },
            )
    finally:
        connection.close()


def _table_count(db_path: Path, table: str) -> int:
    connection = initialize_database(db_path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def test_career_chat_saves_chat_and_auto_applies_suggestions_without_touching_goal_loop() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-chat.sqlite3"
        soul_path = _soul_file(root)
        _seed_profile_and_project(db_path)
        before_projects = _table_count(db_path, "projects")
        before_goals = _table_count(db_path, "daily_goals")
        before_versions = _table_count(db_path, "goal_versions")

        result = send_career_chat_message(
            db_path,
            {
                "message": "我会 Python 和一点机器学习，想往 AI Agent 方向发展，但精力有限。",
                "available_minutes": 60,
            },
            soul_path=soul_path,
            today=date(2026, 6, 11),
        ).payload

        assert result["session_id"] > 0
        assert isinstance(result["recommendations"], list)
        if result["recommendations"]:
            assert any("Agent" in item["title"] or "Agent" in item["why_it_fits"] for item in result["recommendations"])
            assert all("project_binding" in item for item in result["recommendations"])
        assert result["profile_update_suggestions"]
        assert {item["status"] for item in result["profile_update_suggestions"]} == {"applied"}
        assert result["career_profile_update"]["status"] == "applied"
        assert result["career_profile_update"]["applied_suggestion_count"] == len(result["profile_update_suggestions"])
        assert result["career_profile_update"]["soul_synced"] is True
        assert _table_count(db_path, "projects") == before_projects
        assert _table_count(db_path, "daily_goals") == before_goals
        assert _table_count(db_path, "goal_versions") == before_versions

        history = get_career_chat_history(db_path, int(result["session_id"]))
        assert len(history["messages"]) == 2
        assert history["pending_profile_update_suggestions"] == []
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "## 当前技能点" in soul_text
        assert "## 发展意愿" in soul_text

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
        finally:
            connection.close()
        assert profile["career_profile"]["evidence"]


def test_career_chat_allows_assistant_text_without_recommendation_cards() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-text-only.sqlite3"
        soul_path = _soul_file(root)
        _seed_profile_and_project(db_path)
        captured_messages: list[dict[str, str]] = []
        original_generate = career_chat_module.generate_json_with_fallback

        def fake_generate_json_with_fallback(**kwargs: object) -> SimpleNamespace:
            build_messages = kwargs["build_messages"]
            normalizer = kwargs["normalizer"]
            validator = kwargs["validator"]
            captured_messages.extend(build_messages(""))
            output = {
                "schema_version": "career_chat_response.v1",
                "assistant_message": "我会先帮你判断方向和约束；这轮更适合先补充画像，而不是立刻拆成项目卡片。",
                "recommendations": [],
                "profile_update_suggestions": [],
            }
            normalized = normalizer(output)
            validator(normalized)
            return SimpleNamespace(output=normalized, metadata={"llm_mode_used": "test"})

        career_chat_module.generate_json_with_fallback = fake_generate_json_with_fallback
        try:
            result = send_career_chat_message(
                db_path,
                {"message": "我还没有想清楚发展方向，先帮我判断该补什么信息。"},
                soul_path=soul_path,
                today=date(2026, 6, 11),
            ).payload
        finally:
            career_chat_module.generate_json_with_fallback = original_generate

        assert result["recommendations"] == []
        assert result["profile_update_suggestions"] == []
        assert result["career_profile_update"]["status"] == "skipped"
        history = get_career_chat_history(db_path, int(result["session_id"]))
        assert len(history["messages"]) == 2
        assert history["messages"][1]["recommendations"] == []
        prompt_text = "\n".join(item["content"] for item in captured_messages)
        assert "Return 1 to 3 recommendations" not in prompt_text
        assert "recommendations may be an empty array" in prompt_text
        assert "assistant_message should summarize direction" in prompt_text
        assert "no Markdown heading markers" in prompt_text
        assert "saved automatically" in prompt_text
        assert "not saved automatically" not in prompt_text


def test_career_chat_normalizer_preserves_multiline_summary_and_structured_cards() -> None:
    output = {
        "schema_version": "career_chat_response.v1",
        "assistant_message": "先把方向判断压成两步。\n\n今天只做一个可验证的小实验，先产出记录，再决定要不要扩展。",
        "recommendations": [
            {
                "title": "Mini Agent 记忆实验",
                "why_it_fits": "它能验证你对 Agent 记忆和检索的兴趣，同时范围足够小。",
                "skills_to_build": ["Agent 记忆设计", "检索评估"],
                "estimated_time": "45 分钟完成第一版记录",
                "deliverable": "一份包含输入、输出和观察结果的实验记录",
                "first_step": "先写 3 条规则和 3 个查询样例。",
                "project_binding": {
                    "kind": "existing_project",
                    "project_name": "Agent evaluation",
                    "reason": "承接当前 Agent evaluation 项目。",
                },
                "risks": "容易扩成完整系统，需要只保留最小验证。",
                "not_now_reason": "如果今天精力不足，先记录候选，不做工程化。",
            }
        ],
        "profile_update_suggestions": [],
    }

    normalized = career_chat_module.normalize_career_chat_response(output)
    career_chat_module.validate_career_chat_response(normalized)

    assert "\n\n" in normalized["assistant_message"]
    assert "Mini Agent 记忆实验" not in normalized["assistant_message"]
    assert normalized["recommendations"][0]["title"] == "Mini Agent 记忆实验"
    assert normalized["recommendations"][0]["deliverable"]
    assert normalized["recommendations"][0]["project_binding"]["project_name"] == "Agent evaluation"


def test_career_chat_project_binding_requires_exact_active_project_name() -> None:
    output = {
        "schema_version": "career_chat_response.v1",
        "assistant_message": "今天更适合把已有项目切出一个可验证实验。",
        "recommendations": [
            {
                "title": "奖励机制对比实验",
                "why_it_fits": "它能把当前项目推进成可复查实验。",
                "skills_to_build": ["实验设计"],
                "estimated_time": "60 分钟",
                "deliverable": "一份实验记录",
                "first_step": "先列出两个对比条件。",
                "project_binding": {
                    "kind": "existing_project",
                    "project_name": "Agent evaluation",
                    "reason": "精确命中 active 项目。",
                },
                "risks": "范围可能扩大。",
                "not_now_reason": "时间不足时先记录候选。",
            }
        ],
        "profile_update_suggestions": [],
    }

    normalized = career_chat_module.normalize_career_chat_response(output)
    career_chat_module.validate_career_chat_response(normalized, active_project_names=["Agent evaluation"])

    normalized["recommendations"][0]["project_binding"]["project_name"] = "Agent eval"
    try:
        career_chat_module.validate_career_chat_response(normalized, active_project_names=["Agent evaluation"])
    except ValueError as exc:
        assert "unknown_existing_project" in str(exc)
    else:
        raise AssertionError("existing_project binding must exactly match an active project name")


def test_career_chat_new_project_binding_cannot_collide_with_active_project() -> None:
    output = {
        "schema_version": "career_chat_response.v1",
        "assistant_message": "如果是新方向，项目名需要和 active 项目区分开。",
        "recommendations": [
            {
                "title": "作品证据页",
                "why_it_fits": "它适合作为独立成长证据。",
                "skills_to_build": ["作品化表达"],
                "estimated_time": "90 分钟",
                "deliverable": "一页作品证据",
                "first_step": "先定义页面结构。",
                "project_binding": {
                    "kind": "new_project",
                    "project_name": "Agent evaluation",
                    "reason": "错误地撞上 active 项目。",
                },
                "risks": "会和已有项目混淆。",
                "not_now_reason": "可以先记为候选。",
            }
        ],
        "profile_update_suggestions": [],
    }

    normalized = career_chat_module.normalize_career_chat_response(output)
    try:
        career_chat_module.validate_career_chat_response(normalized, active_project_names=["Agent evaluation"])
    except ValueError as exc:
        assert "new_project_collides" in str(exc)
    else:
        raise AssertionError("new_project binding must not reuse an active project name")


def test_career_chat_rejects_card_promise_without_structured_cards() -> None:
    output = {
        "schema_version": "career_chat_response.v1",
        "assistant_message": "下面给出两个可立即启动的最小项目卡片，你可以择一加入 active。",
        "recommendations": [],
        "profile_update_suggestions": [],
    }

    normalized = career_chat_module.normalize_career_chat_response(output)
    try:
        career_chat_module.validate_career_chat_response(normalized)
    except ValueError as exc:
        assert "promised_cards" in str(exc)
    else:
        raise AssertionError("card promise without recommendations should fail validation")


def test_legacy_profile_suggestion_apply_updates_structured_profile_and_soul() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-legacy-apply.sqlite3"
        soul_path = _soul_file(root)
        _seed_profile_and_project(db_path)
        connection = initialize_database(db_path)
        try:
            with connection:
                session_id = repo.create_career_chat_session(connection, title="legacy pending")
                message_id = repo.create_career_chat_message(
                    connection,
                    session_id=session_id,
                    role="assistant",
                    content="历史待确认画像建议。",
                    recommendations=[],
                    profile_update_suggestions=[],
                    context_snapshot={"source": "legacy-test"},
                )
                suggestion_id = repo.create_career_profile_update_suggestion(
                    connection,
                    session_id=session_id,
                    message_id=message_id,
                    category="development_intentions",
                    suggestion_payload={
                        "category": "development_intentions",
                        "items": ["希望发展 AI Agent 系统设计能力"],
                        "evidence": "历史 pending 记录。",
                        "reason": "用于验证旧确认接口兼容性。",
                    },
                )
        finally:
            connection.close()

        applied = decide_career_profile_suggestion(
            db_path,
            {"suggestion_id": suggestion_id, "decision": "apply"},
            soul_path=soul_path,
        ).payload

        assert applied["status"] == "applied"
        assert applied["soul_synced"] is True
        assert applied["career_profile"]
        soul_text = soul_path.read_text(encoding="utf-8")
        assert "## 当前技能点" in soul_text
        assert "## 性格与工作方式" in soul_text
        assert "## 发展意愿" in soul_text
        assert "## 职业价值观与约束" in soul_text

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            suggestion = repo.get_career_profile_update_suggestion(connection, suggestion_id)
        finally:
            connection.close()
        assert suggestion["status"] == "applied"
        assert profile["career_profile"]["evidence"]


def test_legacy_profile_suggestion_dismiss_does_not_update_profile() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-legacy-dismiss.sqlite3"
        soul_path = _soul_file(root)
        _seed_profile_and_project(db_path)
        before = initialize_database(db_path)
        try:
            before_profile = repo.get_user_profile(before)["career_profile"]
        finally:
            before.close()
        connection = initialize_database(db_path)
        try:
            with connection:
                session_id = repo.create_career_chat_session(connection, title="legacy pending")
                message_id = repo.create_career_chat_message(
                    connection,
                    session_id=session_id,
                    role="assistant",
                    content="历史待确认画像建议。",
                    recommendations=[],
                    profile_update_suggestions=[],
                    context_snapshot={"source": "legacy-test"},
                )
                suggestion_id = repo.create_career_profile_update_suggestion(
                    connection,
                    session_id=session_id,
                    message_id=message_id,
                    category="development_intentions",
                    suggestion_payload={
                        "category": "development_intentions",
                        "items": ["可能想看 AI Agent"],
                        "evidence": "历史 pending 记录。",
                        "reason": "用于验证旧忽略接口兼容性。",
                    },
                )
        finally:
            connection.close()

        dismissed = decide_career_profile_suggestion(
            db_path,
            {"suggestion_id": suggestion_id, "decision": "dismiss"},
            soul_path=soul_path,
        ).payload

        connection = initialize_database(db_path)
        try:
            profile = repo.get_user_profile(connection)
            suggestion = repo.get_career_profile_update_suggestion(connection, suggestion_id)
        finally:
            connection.close()
        assert dismissed["status"] == "dismissed"
        assert suggestion["status"] == "dismissed"
        assert profile["career_profile"] == before_profile


def test_career_chat_with_missing_soul_and_empty_profile_still_gives_conservative_advice() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-empty.sqlite3"
        missing_soul_path = root / "missing-SOUL.md"

        result = send_career_chat_message(
            db_path,
            {"message": "我想做职业规划，但还没整理技能和方向。"},
            soul_path=missing_soul_path,
            today=date(2026, 6, 11),
        ).payload

        assert isinstance(result["recommendations"], list)
        if result["recommendations"]:
            assert "画像" in result["recommendations"][0]["title"] or result["profile_update_suggestions"]
        assert result["profile_update_suggestions"]
        assert {item["status"] for item in result["profile_update_suggestions"]} == {"applied"}
        assert result["career_profile_update"]["status"] == "applied"


def main() -> None:
    test_career_chat_saves_chat_and_auto_applies_suggestions_without_touching_goal_loop()
    test_career_chat_allows_assistant_text_without_recommendation_cards()
    test_career_chat_normalizer_preserves_multiline_summary_and_structured_cards()
    test_career_chat_project_binding_requires_exact_active_project_name()
    test_career_chat_new_project_binding_cannot_collide_with_active_project()
    test_career_chat_rejects_card_promise_without_structured_cards()
    test_legacy_profile_suggestion_apply_updates_structured_profile_and_soul()
    test_legacy_profile_suggestion_dismiss_does_not_update_profile()
    test_career_chat_with_missing_soul_and_empty_profile_still_gives_conservative_advice()
    print("PASS: career chat service preserves goal loop and auto-applies profile updates")


if __name__ == "__main__":
    main()
