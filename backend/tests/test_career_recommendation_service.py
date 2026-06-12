from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import initialize_database  # noqa: E402
from backend.services.career_chat_service import get_career_chat_history  # noqa: E402
from backend.services.career_recommendation_service import adopt_career_recommendation  # noqa: E402


WORKDAY = date(2026, 6, 9)
WEEKEND = date(2026, 6, 13)


def _soul_file(root: Path) -> Path:
    path = root / "SOUL.md"
    path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                "1. MiniAgent-RL：当前进度：已有 checkpoint。项目最终目标：形成可复查的 RL 实验记录。",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付。",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _recommendation(
    title: str = "RL 奖励机制对比实验",
    *,
    why_it_fits: str = "它能把奖励机制理解转成可复查记录。",
    project_binding: dict | None = None,
    include_project_binding: bool = True,
) -> dict:
    recommendation = {
        "title": title,
        "why_it_fits": why_it_fits,
        "skills_to_build": ["深度强化学习", "实验方法论"],
        "estimated_time": "90-120 分钟",
        "deliverable": "《RL 奖励机制对比报告》",
        "first_step": "从已有 checkpoint 中选择一个基线，设计 2 个奖励变体。",
        "risks": "训练算力和评估一致性需要提前确认。",
        "not_now_reason": "如果今天时间不足，先记录候选，不急着展开。",
    }
    if include_project_binding:
        recommendation["project_binding"] = project_binding or {
            "kind": "existing_project",
            "project_name": "MiniAgent-RL",
            "reason": "这个实验承接当前 MiniAgent-RL 项目。",
        }
    return recommendation


def _seed_project_and_message(db_path: Path, recommendations: list[dict] | None = None) -> tuple[int, int, int]:
    connection = initialize_database(db_path)
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build AI Agent and RL experiment ability.",
                current_focus_projects=["MiniAgent-RL"],
            )
            project_id = repo.create_project(
                connection,
                name="MiniAgent-RL",
                priority="P1",
                role="support",
                status="active",
                source_payload={
                    "target_goal": "形成可复查的 RL 实验记录。",
                    "today_goal": "整理现有 checkpoint。",
                },
            )
            daily_goal_id = repo.create_daily_goal(
                connection,
                project_id=project_id,
                goal_date=WORKDAY.isoformat(),
                context_snapshot={"source": "primary-test"},
                generated_at=f"{WORKDAY.isoformat()} 09:00:00",
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=daily_goal_id,
                version_no=1,
                is_active=1,
                main_goal="整理 MiniAgent-RL 的现有 checkpoint",
                goal_reason="Seeded primary goal.",
                success_criteria=["列出 checkpoint", "记录下一步"],
                estimated_minutes=60,
                difficulty_level=2,
                minimum_version="checkpoint 清单存在。",
                stretch_challenge="补充一条验证记录。",
                avoid_today=json.dumps(["不要新增训练管线"], ensure_ascii=False),
                goal_type="planning",
                revision_source="initial_generation",
                critic_result={},
                prompt_version="test",
            )
            session_id = repo.create_career_chat_session(connection, title="Career")
            message_id = repo.create_career_chat_message(
                connection,
                session_id=session_id,
                role="assistant",
                content="可以把建议加入今日目标。",
                recommendations=recommendations or [_recommendation()],
                profile_update_suggestions=[],
                context_snapshot={"source": "test"},
            )
    finally:
        connection.close()
    return project_id, daily_goal_id, message_id


def test_adopting_existing_project_recommendation_adds_independent_daily_goal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation.sqlite3"
        soul_path = _soul_file(root)
        project_id, primary_goal_id, message_id = _seed_project_and_message(db_path)

        result = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["project"]["id"] == project_id
        extra_goal_id = result["goal"]["daily_goal"]["id"]
        assert extra_goal_id != primary_goal_id
        assert result["goal"]["daily_goal"]["goal_source"] == "career_recommendation"

        connection = initialize_database(db_path)
        try:
            goals = repo.list_daily_goals_by_date(connection, WORKDAY.isoformat())
            assert [goal["id"] for goal in goals] == [primary_goal_id, extra_goal_id]
            primary = repo.get_goal_with_active_version_by_date_and_project(
                connection,
                WORKDAY.isoformat(),
                project_id,
            )
            assert primary["daily_goal"]["id"] == primary_goal_id
            repo.create_daily_checkin(
                connection,
                daily_goal_id=extra_goal_id,
                checkin_date=WORKDAY.isoformat(),
                week_id="2026-W24",
                completion_text="完成奖励机制对比报告。",
                felt_difficulty=3,
                parsed_completion_rate=1.0,
                completed_items=["report"],
                unfinished_items=[],
                blockers=[],
                actual_outputs=["RL 奖励机制对比报告"],
                processor_snapshot={"source": "test"},
            )
            assert repo.get_daily_goal(connection, extra_goal_id)["status"] == "checked_in"
            assert repo.get_daily_goal(connection, primary_goal_id)["status"] == "active"
        finally:
            connection.close()


def test_new_project_binding_uses_llm_project_name_instead_of_title() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-new-project.sqlite3"
        soul_path = _soul_file(root)
        recommendation = _recommendation(
            "召回率对比实验",
            project_binding={
                "kind": "new_project",
                "project_name": "Career Evidence Lab",
                "reason": "这是一个独立作品证据项目。",
            },
        )
        _seed_project_and_message(db_path, recommendations=[recommendation])

        result = adopt_career_recommendation(
            db_path,
            {"message_id": 1, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["project"]["name"] == "Career Evidence Lab"
        assert result["project"]["name"] != recommendation["title"]


def test_legacy_recommendation_without_binding_still_uses_text_match() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-legacy.sqlite3"
        soul_path = _soul_file(root)
        project_id, _primary_goal_id, message_id = _seed_project_and_message(
            db_path,
            recommendations=[
                _recommendation(
                    "MiniAgent-RL 奖励实验",
                    why_it_fits="它直接承接 MiniAgent-RL 的奖励机制理解。",
                    include_project_binding=False,
                )
            ],
        )

        result = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert result["status"] == "applied"
        assert result["project"]["id"] == project_id


def test_invalid_existing_project_binding_returns_candidates_without_writing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-invalid-binding.sqlite3"
        soul_path = _soul_file(root)
        _seed_project_and_message(
            db_path,
            recommendations=[
                _recommendation(
                    project_binding={
                        "kind": "existing_project",
                        "project_name": "Missing Project",
                        "reason": "LLM picked a project name that is not active.",
                    }
                )
            ],
        )

        result = adopt_career_recommendation(
            db_path,
            {"message_id": 1, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert result["status"] == "needs_project_choice"
        assert [candidate["name"] for candidate in result["candidates"]] == ["MiniAgent-RL"]
        connection = initialize_database(db_path)
        try:
            assert repo.get_career_recommendation_action_by_source(connection, 1, 0) is None
            assert len(repo.list_daily_goals_by_date(connection, WORKDAY.isoformat())) == 1
        finally:
            connection.close()


def test_adopting_same_recommendation_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-idempotent.sqlite3"
        soul_path = _soul_file(root)
        _project_id, _primary_goal_id, message_id = _seed_project_and_message(db_path)

        first = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload
        second = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert second["status"] == "already_applied"
        assert second["action"]["daily_goal_id"] == first["action"]["daily_goal_id"]
        connection = initialize_database(db_path)
        try:
            assert len(repo.list_daily_goals_by_date(connection, WORKDAY.isoformat())) == 2
            history = get_career_chat_history(db_path, 1)
            recommendation = history["messages"][0]["recommendations"][0]
            assert recommendation["adoption"]["daily_goal_id"] == first["action"]["daily_goal_id"]
        finally:
            connection.close()


def test_ambiguous_project_match_returns_candidates_without_writing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-ambiguous.sqlite3"
        soul_path = _soul_file(root)
        _seed_project_and_message(
            db_path,
            recommendations=[_recommendation("MiniAgent-RL 对比实验", include_project_binding=False)],
        )
        connection = initialize_database(db_path)
        try:
            with connection:
                repo.create_project(connection, name="MiniAgent", priority="P2", status="active")
        finally:
            connection.close()

        result = adopt_career_recommendation(
            db_path,
            {"message_id": 1, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload

        assert result["status"] == "needs_project_choice"
        assert len(result["candidates"]) == 2
        connection = initialize_database(db_path)
        try:
            assert repo.get_career_recommendation_action_by_source(connection, 1, 0) is None
        finally:
            connection.close()


def test_weekend_adoption_records_project_then_creates_goal_on_next_workday() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "career-recommendation-weekend.sqlite3"
        soul_path = _soul_file(root)
        _project_id, _primary_goal_id, message_id = _seed_project_and_message(db_path)

        weekend = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WEEKEND,
            soul_path=soul_path,
        ).payload
        assert weekend["status"] == "pending_next_workday"
        assert weekend["action"]["daily_goal_id"] is None

        workday = adopt_career_recommendation(
            db_path,
            {"message_id": message_id, "recommendation_index": 0},
            today=WORKDAY,
            soul_path=soul_path,
        ).payload
        assert workday["status"] == "already_applied"
        assert workday["action"]["daily_goal_id"] is not None


def main() -> None:
    test_adopting_existing_project_recommendation_adds_independent_daily_goal()
    test_new_project_binding_uses_llm_project_name_instead_of_title()
    test_legacy_recommendation_without_binding_still_uses_text_match()
    test_invalid_existing_project_binding_returns_candidates_without_writing()
    test_adopting_same_recommendation_is_idempotent()
    test_ambiguous_project_match_returns_candidates_without_writing()
    test_weekend_adoption_records_project_then_creates_goal_on_next_workday()
    print("PASS: career recommendation adoption creates independent daily goals")


if __name__ == "__main__":
    main()
