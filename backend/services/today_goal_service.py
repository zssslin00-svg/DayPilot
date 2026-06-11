from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.schemas.json_schema import JsonSchemaValidationError
from backend.services.goal_critic import ensure_goal_quality
from backend.services.goal_generation_resources import (
    compact_daily_goal_example,
    daily_goal_output_contract,
    daily_goal_repair_hint,
    normalize_daily_goal_output,
    validate_daily_goal_output,
)
from backend.services.llm_client import generate_json_with_fallback
from backend.services.project_progress_service import ensure_projects_seeded
from backend.services.workday_policy import is_workday


PROMPT_VERSION_MOCK = "goal_generation_v1_mock"
PROMPT_VERSION_DEEPSEEK = "goal_generation_v3_deepseek"
MOCK_MODEL_NAME = "mock-daily-goal-adapter"


@dataclass(frozen=True)
class TodayGoalResult:
    goals: list[dict[str, Any]]
    created_count: int
    carried_over_count: int

    @property
    def goal(self) -> dict[str, Any] | None:
        return self.goals[0] if self.goals else None

    @property
    def created(self) -> bool:
        return self.created_count > 0 or self.carried_over_count > 0


class DailyGoalGenerationError(RuntimeError):
    """Raised when a generated daily goal cannot be validated or persisted."""


@dataclass(frozen=True)
class ProjectTodayGoalRefreshResult:
    status: str
    goal: dict[str, Any] | None
    created_count: int


class MockDailyGoalLLMAdapter:
    """Deterministic adapter with the same contract as the real LLM adapter."""

    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        goal_date = str(context["goal_date"])
        profile = context.get("user_profile") or {}
        project = context.get("project") or {}
        project_goal_constraints = context.get("project_goal_constraints") or {}
        project_today_goal = _compact_text(
            project_goal_constraints.get("today_goal") or project.get("today_goal") or "",
            80,
        )
        ability_state = context.get("ability_state") or {}
        weekly_focus = context.get("weekly_focus") or []
        selected_focus = context.get("selected_weekly_focus")
        tomorrow_direction = _compact_text(context.get("tomorrow_direction") or "", 52)

        focus_for_type = [selected_focus] if selected_focus else weekly_focus
        goal_type = _select_goal_type(tomorrow_direction, focus_for_type)
        estimated_minutes = _clamp_int(
            ability_state.get("default_estimated_minutes")
            or profile.get("default_available_minutes")
            or 75,
            30,
            150,
        )
        difficulty = _clamp_int(
            ability_state.get("target_difficulty_level")
            or ability_state.get("current_difficulty")
            or 2,
            1,
            5,
        )
        completion_criteria = [
            "明确本次交付物的输入、输出和验收条件",
            "完成一处可运行或可复查的 DayPilot 项目改动",
            "记录生成时使用的上下文和后续范围限制",
        ]
        minimum_result = "至少产出一个可检查的 DayPilot MVP 改动，并保留生成上下文记录。"
        stretch_challenge = "如果基础目标完成，补充一条窄范围测试或验收记录。"
        do_not_do = [
            "不要实现登录、多用户或外部系统集成",
            "不要同时扩展在线反馈修正和周报生成",
        ]

        if project_today_goal:
            project_name = str(project.get("name") or _first_project(profile) or "当前项目")
            main_goal, project_today_goal = _project_today_goal_main_goal(project_name, project_today_goal)
            primary_driver = "current_project_today_goal"
            tomorrow_handling = "empty_agent_decided"
            continuity_note = (
                "优先使用 SOUL.md 当前项目里的项目今日目标，并将其整理成今天可验收的单一主目标。"
            )
            completion_criteria = [
                _compact_text(f"完成“{project_today_goal}”对应的一个可检查交付物", 140),
                "记录交付物位置、验证结果或后续限制",
                "明确今天不扩展到项目最终目标的完整范围",
            ]
            minimum_result = _compact_text(f"至少留下“{project_today_goal}”的最小代码、文档、笔记或决策记录。", 220)
            stretch_challenge = "完成最低切片后，补充一条验收记录或下一步边界。"
            do_not_do = [
                "不要把项目最终目标整体压成今天的任务",
                "不要扩展到 SOUL 今日目标以外的方向",
            ]
        elif selected_focus:
            focus_text = _daily_focus_slice_text(selected_focus)
            main_goal = f"承接 weekly_focus，交付{focus_text}的今日最小切片"
            primary_driver = "last_week_focus"
            tomorrow_handling = _tomorrow_handling_for_focus(tomorrow_direction)
            continuity_note = _compact_text(
                context.get("focus_selection_reason")
                or "优先承接最近 weekly_focus，并只选择今天可以完成的最小交付物。",
                240,
            )
            completion_criteria = [
                _compact_text(f"完成{focus_text}的一项可检查结果", 140),
                "记录该结果如何承接 weekly_focus",
                "保留今天不扩展的范围说明",
            ]
            minimum_result = _compact_text(f"留下{focus_text}的最小代码、文档或测试记录。", 220)
            stretch_challenge = "完成最低切片后，补充一条验证记录或失败边界。"
            do_not_do = [
                "不要把整条 weekly_focus 当成一日任务",
                "不要新增登录、多用户或外部系统集成",
            ]
        elif tomorrow_direction:
            main_goal = f"把“{tomorrow_direction}”落成一个今日可交付的 DayPilot 工作切片"
            primary_driver = "tomorrow_direction"
            tomorrow_handling = (
                "narrowed_to_daily_scope"
                if _looks_like_large_direction(tomorrow_direction)
                else "used_as_given"
            )
            continuity_note = "使用用户的明日方向作为偏好，但只保留今天可以完成的交付范围。"
        else:
            current_project = str(project.get("name") or _first_project(profile) or "DayPilot MVP")
            main_goal = f"打通 {current_project} 今日目标生成服务的最小可验证闭环"
            primary_driver = "current_project"
            tomorrow_handling = "empty_agent_decided"
            continuity_note = f"围绕项目「{current_project}」生成，每个 active 项目各自生成今日目标。"

        main_goal = _compact_text(main_goal, 120)
        return {
            "schema_version": "daily_goal.v1",
            "goal_date": goal_date,
            "main_goal": main_goal,
            "rationale": (
                "该目标来自 DayPilot 的当前上下文聚合结果，优先保证今天能形成可检查产出。"
                "范围被限制在每日目标闭环内，不扩展到额外系统集成。"
            ),
            "completion_criteria": completion_criteria,
            "estimated_minutes": estimated_minutes,
            "difficulty": difficulty,
            "minimum_acceptable_result": minimum_result,
            "stretch_challenge": stretch_challenge,
            "do_not_do_today": do_not_do,
            "goal_type": goal_type,
            "growth_tags": ["daypilot_mvp", "daily_goal", "agent_workflow"],
            "context_used": {
                "primary_driver": primary_driver,
                "tomorrow_direction_handling": tomorrow_handling,
                "continuity_note": continuity_note,
                "difficulty_reason": "读取 ability_state 的建议难度和默认分钟数，并限制在 MVP 的每日目标范围内。",
            },
        }


def get_or_generate_today_goal(
    db_path: str | Path,
    today: date,
    *,
    soul_path: str | Path | None = None,
) -> TodayGoalResult:
    goal_date = today.isoformat()
    sync_source_goal_id: int | None = None
    connection = initialize_database(db_path)
    try:
        with connection:
            _ensure_default_profile(connection)
            ensure_projects_seeded(connection)
            active_projects = repo.list_projects(connection)
            goals: list[dict[str, Any]] = []
            created_count = 0
            carried_over_count = 0

            for project in active_projects:
                project_id = int(project["id"])
                existing = repo.get_goal_with_active_version_by_date_and_project(
                    connection,
                    goal_date,
                    project_id,
                )
                if existing and existing.get("active_version") is not None:
                    goals.append(_attach_goal_output(existing))
                    continue

                context = _build_generation_context(connection, today, project)
                carry_source = _latest_unfinished_goal_record(context["recent_project_goal_records"])
                if carry_source is not None:
                    goal_output = _carry_over_goal_output(carry_source, today, project)
                    context["carryover_from_goal_id"] = carry_source["daily_goal"]["id"]
                    context["llm_metadata"] = {
                        "prompt_version": PROMPT_VERSION_MOCK,
                        "model_name": MOCK_MODEL_NAME,
                        "llm_mode_used": "carryover",
                    }
                    quality_result = ensure_goal_quality(goal_output, flow="generation")
                    goal_output = quality_result.goal
                    carried_over_count += 1
                else:
                    llm_result = _generate_daily_goal_with_llm(context)
                    context["llm_metadata"] = llm_result.metadata
                    goal_output = llm_result.output
                    quality_result = ensure_goal_quality(goal_output, flow="generation")
                    goal_output = quality_result.goal
                    created_count += 1

                try:
                    validate_daily_goal_output(goal_output)
                except JsonSchemaValidationError as exc:
                    raise DailyGoalGenerationError(str(exc)) from exc

                daily_goal_id = _ensure_daily_goal_record(
                    connection,
                    today,
                    None,
                    context,
                    goal_output,
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=len(repo.list_goal_versions(connection, daily_goal_id)) + 1,
                    is_active=1,
                    main_goal=goal_output["main_goal"],
                    goal_reason=goal_output["rationale"],
                    success_criteria=goal_output["completion_criteria"],
                    estimated_minutes=goal_output["estimated_minutes"],
                    difficulty_level=goal_output["difficulty"],
                    minimum_version=goal_output["minimum_acceptable_result"],
                    stretch_challenge=goal_output["stretch_challenge"],
                    avoid_today=json.dumps(
                        goal_output["do_not_do_today"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    goal_type=goal_output["goal_type"],
                    revision_source="initial_generation",
                    revision_reason=(
                        "Carry over incomplete project goal."
                        if carry_source is not None
                        else "Morning project goal creation."
                    ),
                    critic_result={
                        "schema": "daily_goal.v1",
                        "quality_status": quality_result.quality_status,
                        "review": quality_result.review,
                        "llm_metadata": context["llm_metadata"],
                    },
                    prompt_version=context["llm_metadata"]["prompt_version"],
                )
                _mark_selected_focus_carried(connection, context, daily_goal_id, goal_output)
                if update_project_today_goal_from_output(
                    connection,
                    project_id,
                    goal_output,
                    source="today_goal_generation",
                    daily_goal_id=daily_goal_id,
                ):
                    sync_source_goal_id = sync_source_goal_id or daily_goal_id

                generated = repo.get_goal_with_active_version_by_date_and_project(
                    connection,
                    goal_date,
                    project_id,
                )
                if generated is None or generated.get("active_version") is None:
                    raise DailyGoalGenerationError("Generated goal was not persisted.")
                goals.append(_attach_goal_output(generated))

        if sync_source_goal_id is not None:
            sync_current_projects_to_soul_if_requested(db_path, soul_path, sync_source_goal_id)
        return TodayGoalResult(
            goals=goals,
            created_count=created_count,
            carried_over_count=carried_over_count,
        )
    except sqlite3.DatabaseError as exc:
        raise DailyGoalGenerationError(str(exc)) from exc
    finally:
        connection.close()


def regenerate_today_goal(
    db_path: str | Path,
    today: date,
    *,
    soul_path: str | Path | None = None,
) -> TodayGoalResult:
    """Force a fresh daily goal version for every active project."""

    goal_date = today.isoformat()
    sync_source_goal_id: int | None = None
    connection = initialize_database(db_path)
    try:
        with connection:
            _ensure_default_profile(connection)
            ensure_projects_seeded(connection)
            active_projects = repo.list_projects(connection)
            goals: list[dict[str, Any]] = []
            regenerated_count = 0

            for project in active_projects:
                project_id = int(project["id"])
                existing = repo.get_goal_with_active_version_by_date_and_project(
                    connection,
                    goal_date,
                    project_id,
                )
                context = _build_generation_context(connection, today, project)
                llm_result = _generate_daily_goal_with_llm(context)
                context["llm_metadata"] = llm_result.metadata
                goal_output = llm_result.output
                quality_result = ensure_goal_quality(goal_output, flow="generation")
                goal_output = quality_result.goal

                try:
                    validate_daily_goal_output(goal_output)
                except JsonSchemaValidationError as exc:
                    raise DailyGoalGenerationError(str(exc)) from exc

                daily_goal_id = _ensure_daily_goal_record(
                    connection,
                    today,
                    existing["daily_goal"] if existing else None,
                    context,
                    goal_output,
                    preserve_generated_at=False,
                )
                repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=len(repo.list_goal_versions(connection, daily_goal_id)) + 1,
                    is_active=1,
                    main_goal=goal_output["main_goal"],
                    goal_reason=goal_output["rationale"],
                    success_criteria=goal_output["completion_criteria"],
                    estimated_minutes=goal_output["estimated_minutes"],
                    difficulty_level=goal_output["difficulty"],
                    minimum_version=goal_output["minimum_acceptable_result"],
                    stretch_challenge=goal_output["stretch_challenge"],
                    avoid_today=json.dumps(
                        goal_output["do_not_do_today"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    goal_type=goal_output["goal_type"],
                    revision_source="system_regeneration",
                    revision_reason="Manual frontend refresh requested fresh LLM generation.",
                    critic_result={
                        "schema": "daily_goal.v1",
                        "quality_status": quality_result.quality_status,
                        "review": quality_result.review,
                        "llm_metadata": context["llm_metadata"],
                    },
                    prompt_version=context["llm_metadata"]["prompt_version"],
                )
                _mark_selected_focus_carried(connection, context, daily_goal_id, goal_output)
                if update_project_today_goal_from_output(
                    connection,
                    project_id,
                    goal_output,
                    source="today_goal_regeneration",
                    daily_goal_id=daily_goal_id,
                ):
                    sync_source_goal_id = sync_source_goal_id or daily_goal_id

                generated = repo.get_goal_with_active_version_by_date_and_project(
                    connection,
                    goal_date,
                    project_id,
                )
                if generated is None or generated.get("active_version") is None:
                    raise DailyGoalGenerationError("Regenerated goal was not persisted.")
                goals.append(_attach_goal_output(generated))
                regenerated_count += 1

        if sync_source_goal_id is not None:
            sync_current_projects_to_soul_if_requested(db_path, soul_path, sync_source_goal_id)
        return TodayGoalResult(
            goals=goals,
            created_count=regenerated_count,
            carried_over_count=0,
        )
    except sqlite3.DatabaseError as exc:
        raise DailyGoalGenerationError(str(exc)) from exc
    finally:
        connection.close()


def refresh_today_goal_for_project(
    db_path: str | Path,
    today: date,
    project_id: int,
    *,
    force: bool,
    revision_reason: str,
    soul_path: str | Path | None = None,
) -> ProjectTodayGoalRefreshResult:
    """Generate or refresh today's goal for one active project only."""

    if not is_workday(today):
        return ProjectTodayGoalRefreshResult(status="skipped_non_workday", goal=None, created_count=0)

    goal_date = today.isoformat()
    connection = initialize_database(db_path)
    try:
        with connection:
            _ensure_default_profile(connection)
            ensure_projects_seeded(connection)
            project = repo.get_project(connection, int(project_id))
            if project is None or str(project.get("status") or "") != "active":
                return ProjectTodayGoalRefreshResult(status="skipped_inactive", goal=None, created_count=0)

            existing = repo.get_goal_with_active_version_by_date_and_project(
                connection,
                goal_date,
                int(project["id"]),
            )
            if existing and existing.get("active_version") is not None and not force:
                return ProjectTodayGoalRefreshResult(
                    status="kept",
                    goal=_attach_goal_output(existing),
                    created_count=0,
                )

            had_active_goal = bool(existing and existing.get("active_version") is not None)
            context = _build_generation_context(connection, today, project)
            llm_result = _generate_daily_goal_with_llm(context)
            context["llm_metadata"] = llm_result.metadata
            goal_output = llm_result.output
            quality_result = ensure_goal_quality(goal_output, flow="generation")
            goal_output = quality_result.goal

            try:
                validate_daily_goal_output(goal_output)
            except JsonSchemaValidationError as exc:
                raise DailyGoalGenerationError(str(exc)) from exc

            daily_goal_id = _ensure_daily_goal_record(
                connection,
                today,
                existing["daily_goal"] if existing else None,
                context,
                goal_output,
                preserve_generated_at=False,
            )
            repo.create_goal_version(
                connection,
                daily_goal_id=daily_goal_id,
                version_no=len(repo.list_goal_versions(connection, daily_goal_id)) + 1,
                is_active=1,
                main_goal=goal_output["main_goal"],
                goal_reason=goal_output["rationale"],
                success_criteria=goal_output["completion_criteria"],
                estimated_minutes=goal_output["estimated_minutes"],
                difficulty_level=goal_output["difficulty"],
                minimum_version=goal_output["minimum_acceptable_result"],
                stretch_challenge=goal_output["stretch_challenge"],
                avoid_today=json.dumps(
                    goal_output["do_not_do_today"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                goal_type=goal_output["goal_type"],
                revision_source="system_regeneration" if had_active_goal else "initial_generation",
                revision_reason=revision_reason,
                critic_result={
                    "schema": "daily_goal.v1",
                    "quality_status": quality_result.quality_status,
                    "review": quality_result.review,
                    "llm_metadata": context["llm_metadata"],
                },
                prompt_version=context["llm_metadata"]["prompt_version"],
            )
            _mark_selected_focus_carried(connection, context, daily_goal_id, goal_output)
            should_sync_soul = update_project_today_goal_from_output(
                connection,
                int(project["id"]),
                goal_output,
                source="today_goal_project_refresh",
                daily_goal_id=daily_goal_id,
            )

            generated = repo.get_goal_with_active_version_by_date_and_project(
                connection,
                goal_date,
                int(project["id"]),
            )
            if generated is None or generated.get("active_version") is None:
                raise DailyGoalGenerationError("Generated goal was not persisted.")

        if should_sync_soul:
            sync_current_projects_to_soul_if_requested(db_path, soul_path, daily_goal_id)
        return ProjectTodayGoalRefreshResult(
            status="refreshed" if had_active_goal else "created",
            goal=_attach_goal_output(generated),
            created_count=1,
        )
    except sqlite3.DatabaseError as exc:
        raise DailyGoalGenerationError(str(exc)) from exc
    finally:
        connection.close()


def _generate_daily_goal_with_llm(context: dict[str, Any]):
    return generate_json_with_fallback(
        task_name="daily_goal_generation",
        prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
        prompt_version_mock=PROMPT_VERSION_MOCK,
        mock_model_name=MOCK_MODEL_NAME,
        build_messages=lambda soul, ctx=context: _daily_goal_messages(ctx, soul),
        mock_generate=lambda ctx=context: MockDailyGoalLLMAdapter().generate(ctx),
        validator=validate_daily_goal_output,
        normalizer=lambda output, ctx=context: normalize_daily_goal_output(
            output,
            ctx,
        ),
        repair_hint=daily_goal_repair_hint(),
    )


def _daily_goal_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot Goal Generator. Return only one JSON object that matches daily_goal.v1.
The response must be valid json and must not include Markdown fences.
Use concise Chinese for user-facing fields. Keep exactly one main goal for the current project.
Do not choose between projects. The current project is already selected.
Strictly follow the output_contract in the user message. In particular:
- If context.project_goal_constraints.today_goal is non-empty, use it as the strongest project-level constraint and narrow it into one checkable daily goal.
- growth_tags must be English lowercase slugs, never Chinese.
- context_used must include primary_driver, tomorrow_direction_handling, continuity_note, and difficulty_reason.
- context_used must not include extra keys such as project_priority or weekly_focus_alignment.
- do_not_do_today must be an array of strings, never one string.
"""
    user = {
        "task": "Generate one workday daily goal for the provided active project.",
        "schema_version": "daily_goal.v1",
        "required_fields": [
            "schema_version",
            "goal_date",
            "main_goal",
            "rationale",
            "completion_criteria",
            "estimated_minutes",
            "difficulty",
            "minimum_acceptable_result",
            "stretch_challenge",
            "do_not_do_today",
            "goal_type",
            "growth_tags",
            "context_used",
        ],
        "constraints": {
            "estimated_minutes": "integer 30-150",
            "difficulty": "integer 1-5",
            "completion_criteria": "2-5 concrete checkable items",
            "goal_type": "one of design,coding,documentation,review,learning,research,debugging,testing,planning",
        },
        "output_contract": daily_goal_output_contract(),
        "valid_example": compact_daily_goal_example(),
        "context": _json_safe_context(context),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _build_generation_context(
    connection: sqlite3.Connection,
    today: date,
    project: dict[str, Any],
) -> dict[str, Any]:
    profile = _ensure_default_profile(connection)
    ensure_projects_seeded(connection)
    projects = repo.list_projects(connection)
    ability_state = _ensure_current_ability_state(connection, today)
    goal_date = today.isoformat()
    week_id = repo.week_id_for_date(today)
    project_id = int(project["id"])

    recent_goals = repo.list_recent_daily_goal_records(connection, goal_date, limit=14)
    recent_project_goals = repo.list_recent_daily_goal_records_for_project(
        connection,
        goal_date,
        project_id,
        limit=7,
    )
    recent_checkins = [
        item["daily_checkin"]
        for item in recent_project_goals
        if item.get("daily_checkin") is not None
    ]
    recent_feedback = repo.list_recent_feedback_messages(connection, goal_date, limit=20)
    recent_project_progress: list[dict[str, Any]] = []
    weekly_focus = repo.list_weekly_focus_by_target_week(connection, week_id)
    if not weekly_focus:
        weekly_focus = repo.list_recent_weekly_focus(connection, limit=5)
    selected_focus, focus_selection_reason = _select_weekly_focus(
        weekly_focus,
        recent_goals=recent_goals,
        today=today,
        tomorrow_direction=_latest_tomorrow_direction(recent_checkins),
    )

    return {
        "goal_date": goal_date,
        "week_id": week_id,
        "user_profile": profile,
        "project": project,
        "project_goal_constraints": {
            "target_goal": repo.project_target_goal(project),
            "today_goal": repo.project_today_goal(project),
        },
        "projects": projects,
        "recent_daily_goals": recent_goals,
        "recent_project_goal_records": recent_project_goals,
        "recent_checkins": recent_checkins,
        "recent_feedback_messages": recent_feedback,
        "recent_project_progress_events": recent_project_progress,
        "ability_state": ability_state,
        "weekly_focus": weekly_focus,
        "selected_weekly_focus": selected_focus,
        "focus_selection_reason": focus_selection_reason,
        "focus_deviation_log": _build_focus_deviation_log(
            today,
            selected_focus,
            _latest_tomorrow_direction(recent_checkins),
            focus_selection_reason,
        ),
        "tomorrow_direction": _latest_tomorrow_direction(recent_checkins),
    }


def _ensure_daily_goal_record(
    connection: sqlite3.Connection,
    today: date,
    existing_daily_goal: dict[str, Any] | None,
    context: dict[str, Any],
    goal_output: dict[str, Any],
    *,
    preserve_generated_at: bool = True,
) -> int:
    snapshot = _context_snapshot(context, goal_output)
    now = _now_text()
    if existing_daily_goal is not None:
        updated = repo.update_daily_goal(
            connection,
            int(existing_daily_goal["id"]),
            context_snapshot=snapshot,
            generated_at=(existing_daily_goal.get("generated_at") or now) if preserve_generated_at else now,
            status="active",
        )
        return int(updated["id"])

    return repo.create_daily_goal(
        connection,
        project_id=int(context["project"]["id"]),
        goal_date=today.isoformat(),
        context_snapshot=snapshot,
        generated_at=now,
        status="active",
    )


def update_project_today_goal_from_output(
    connection: sqlite3.Connection,
    project_id: int,
    goal_output: dict[str, Any],
    *,
    source: str,
    daily_goal_id: int,
) -> bool:
    today_goal = str(goal_output.get("main_goal") or "").strip()
    if not today_goal:
        return False
    project = repo.get_project(connection, int(project_id))
    if project is None:
        return False
    if repo.project_today_goal(project) == today_goal:
        return False
    next_state = repo.merge_project_state(
        project.get("project_state"),
        {"today_goal": today_goal},
        updated_from={
            "source": source,
            "daily_goal_id": daily_goal_id,
        },
    )
    repo.update_project(connection, int(project_id), project_state=next_state)
    return True


def sync_current_projects_to_soul_if_requested(
    db_path: str | Path,
    soul_path: str | Path | None,
    source_daily_goal_id: int,
) -> None:
    if soul_path is None:
        return
    try:
        from backend.services.project_lifecycle_service import sync_current_projects_to_soul

        sync_current_projects_to_soul(db_path, soul_path=soul_path)
    except Exception as exc:  # noqa: BLE001 - goal persistence is already complete
        from backend.services.soul_sync_service import enqueue_soul_sync_retry

        enqueue_soul_sync_retry(
            db_path,
            job_type="project_lifecycle",
            source_table="daily_goals",
            source_id=source_daily_goal_id,
            payload={
                "daily_goal_id": source_daily_goal_id,
                "action": "sync_today_goal_to_soul",
            },
            error=_safe_error(exc),
        )


def _context_snapshot(context: dict[str, Any], goal_output: dict[str, Any]) -> dict[str, Any]:
    llm_metadata = context.get("llm_metadata") or {}
    return {
        "schema_version": "today_goal_context.v1",
        "profile_id": context["user_profile"]["id"],
        "ability_state_id": context["ability_state"]["id"],
        "project_id": context["project"]["id"],
        "project_name": context["project"]["name"],
        "project_target_goal": repo.project_target_goal(context["project"]),
        "project_today_goal": repo.project_today_goal(context["project"]),
        "project_state_hash": repo.project_state_hash(context["project"]),
        "recent_daily_goal_ids": [
            item["daily_goal"]["id"] for item in context["recent_daily_goals"]
        ],
        "recent_project_goal_ids": [
            item["daily_goal"]["id"] for item in context["recent_project_goal_records"]
        ],
        "recent_checkin_ids": [item["id"] for item in context["recent_checkins"]],
        "recent_feedback_message_ids": [
            item["id"] for item in context["recent_feedback_messages"]
        ],
        "weekly_focus_ids": [item["id"] for item in context["weekly_focus"]],
        "project_ids": [item["id"] for item in context.get("projects", [])],
        "carryover_from_goal_id": context.get("carryover_from_goal_id"),
        "selected_weekly_focus_id": (
            context["selected_weekly_focus"]["id"] if context.get("selected_weekly_focus") else None
        ),
        "focus_selection_reason": context.get("focus_selection_reason"),
        "focus_deviation_log": context.get("focus_deviation_log"),
        "tomorrow_direction": context.get("tomorrow_direction"),
        "prompt_version": llm_metadata.get("prompt_version") or PROMPT_VERSION_MOCK,
        "model_name": llm_metadata.get("model_name") or MOCK_MODEL_NAME,
        "llm_metadata": llm_metadata,
        "llm_mode": llm_metadata.get("llm_mode_used"),
        "soul_loaded": llm_metadata.get("soul_loaded"),
        "fallback_reason": llm_metadata.get("fallback_reason"),
        "goal_output_context_used": goal_output["context_used"],
    }


def _attach_goal_output(goal_record: dict[str, Any]) -> dict[str, Any]:
    result = dict(goal_record)
    result["goal_output"] = goal_output_from_record(goal_record)
    return result


def goal_output_from_record(goal_record: dict[str, Any]) -> dict[str, Any] | None:
    daily_goal = goal_record.get("daily_goal")
    active_version = goal_record.get("active_version")
    if not daily_goal or not active_version:
        return None

    context_snapshot = daily_goal.get("context_snapshot") or {}
    criteria = _ensure_string_list(
        active_version.get("success_criteria"),
        fallback=[
            "完成一个可人工检查的核心交付物",
            "记录验收结果和后续范围限制",
        ],
    )
    return {
        "schema_version": "daily_goal.v1",
        "goal_date": daily_goal["goal_date"],
        "main_goal": active_version["main_goal"],
        "rationale": active_version.get("goal_reason") or "该目标来自已保存的 DayPilot 每日目标版本。",
        "completion_criteria": criteria,
        "estimated_minutes": _clamp_int(active_version.get("estimated_minutes") or 60, 30, 150),
        "difficulty": _clamp_int(active_version.get("difficulty_level") or 2, 1, 5),
        "minimum_acceptable_result": active_version["minimum_version"],
        "stretch_challenge": active_version.get("stretch_challenge") or "完成基础目标后补充一条验收记录。",
        "do_not_do_today": _ensure_string_list(
            active_version.get("avoid_today"),
            fallback=["不要扩展到 MVP 范围外的功能"],
        )[:4],
        "goal_type": _schema_goal_type(active_version.get("goal_type")),
        "growth_tags": _growth_tags_from_record(active_version),
        "context_used": context_snapshot.get("goal_output_context_used")
        or {
            "primary_driver": "agent_decision",
            "tomorrow_direction_handling": "empty_agent_decided",
            "continuity_note": "该目标来自已保存版本，缺少原始上下文时使用默认说明。",
            "difficulty_reason": "难度来自已保存目标版本，并被限制在 1-5 范围内。",
        },
    }


def _latest_unfinished_goal_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in records:
        checkin = record.get("daily_checkin")
        if checkin is None:
            return record
        if str(checkin.get("completion_status") or "completed") != "completed":
            return record
    return None


def _carry_over_goal_output(
    source_record: dict[str, Any],
    today: date,
    project: dict[str, Any],
) -> dict[str, Any]:
    output = goal_output_from_record(source_record)
    if output is None:
        raise DailyGoalGenerationError("Cannot carry over a goal without active version.")
    carried = dict(output)
    project_name = str(project.get("name") or "当前项目")
    carried["goal_date"] = today.isoformat()
    carried["main_goal"] = _compact_text(
        f"继续完成「{project_name}」未完成目标：{_strip_carryover_prefix(str(output['main_goal']))}",
        120,
    )
    carried["rationale"] = _compact_text(
        f"该项目上一工作日目标尚未完成，今天先承接完成，再生成新的推进目标。{output.get('rationale') or ''}",
        260,
    )
    carried["context_used"] = {
        **(output.get("context_used") or {}),
        "primary_driver": "recent_unfinished_work",
        "tomorrow_direction_handling": "partially_used",
        "continuity_note": f"项目「{project_name}」显式未完成或缺少 check-in，按规则在今天继续完成。",
    }
    return carried


def _strip_carryover_prefix(text: str) -> str:
    cleaned = re.sub(r'继续完成[「『"“][^」』"”]+[」』"”]未完成目标[：:]\s*', "", text).strip()
    cleaned = re.sub(r"继续完成未完成目标[：:]\s*", "", cleaned).strip()
    return cleaned or text.strip()


def _project_today_goal_main_goal(project_name: str, today_goal: str) -> tuple[str, str]:
    today_goal = str(today_goal or "").strip()
    unwrapped = today_goal
    wrapper_pattern = re.compile(r"^围绕「[^」]+」交付[:：]\s*")
    while True:
        next_value = wrapper_pattern.sub("", unwrapped, count=1).strip()
        if next_value == unwrapped:
            break
        unwrapped = next_value
    slice_text = unwrapped or today_goal
    return _compact_text(f"围绕「{project_name}」交付：{slice_text}", 120), _compact_text(slice_text, 80)


def _ensure_default_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    profile = repo.get_user_profile(connection)
    if profile is not None:
        return profile

    profile_id = repo.create_user_profile(
        connection,
        id=1,
        display_name="DayPilot User",
        long_term_direction="建立稳定的个人工作日每日目标和周报复盘闭环。",
        current_focus_projects=["DayPilot MVP"],
        goal_preferences={
            "goal_type_weights": {
                "coding": 0.4,
                "design": 0.25,
                "documentation": 0.2,
                "review": 0.15,
            }
        },
        avoid_patterns=["目标太虚", "一天多个主目标", "没有最低可接受成果"],
        default_available_minutes=90,
        timezone="Asia/Shanghai",
        workday_rule={"days": [1, 2, 3, 4, 5]},
    )
    profile = repo.get_user_profile(connection, profile_id)
    if profile is None:
        raise DailyGoalGenerationError("Default user profile was not persisted.")
    return profile


def _ensure_current_ability_state(connection: sqlite3.Connection, today: date) -> dict[str, Any]:
    ability_state = repo.get_current_ability_state(connection)
    if ability_state is not None:
        return ability_state

    ability_state_id = repo.create_ability_state(
        connection,
        state_date=today.isoformat(),
        current_difficulty=2.0,
        target_difficulty_level=2,
        recent_completion_rate=None,
        recent_felt_difficulty_avg=None,
        default_estimated_minutes=60,
        preferred_goal_type_weights={
            "coding": 0.35,
            "design": 0.25,
            "documentation": 0.2,
            "review": 0.1,
            "planning": 0.1,
        },
        short_term_preferences={},
        long_term_preferences_snapshot={},
        avoid_patterns_snapshot=["目标太虚", "没有可交付物"],
        adjustment_direction="initial",
        update_reason="Initial ability state for first daily goal generation.",
        is_current=1,
    )
    ability_state = repo.get_ability_state(connection, ability_state_id)
    if ability_state is None:
        raise DailyGoalGenerationError("Default ability state was not persisted.")
    return ability_state


def _latest_tomorrow_direction(checkins: list[dict[str, Any]]) -> str | None:
    for checkin in checkins:
        direction = str(checkin.get("tomorrow_direction") or "").strip()
        if direction:
            return direction
    return None


def _select_weekly_focus(
    weekly_focus: list[dict[str, Any]],
    *,
    recent_goals: list[dict[str, Any]],
    today: date,
    tomorrow_direction: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if not weekly_focus:
        return None, "没有 active weekly_focus，回退到用户画像和近期记录生成。"

    latest_focus_id = _latest_selected_focus_id(recent_goals)
    scored: list[tuple[int, dict[str, Any], str]] = []
    for focus in weekly_focus:
        payload = focus.get("context_payload") or {}
        handoff = payload.get("handoff") or {}
        status_after_checkin = str(handoff.get("status_after_checkin") or "")
        if status_after_checkin == "completed":
            continue
        history = handoff.get("daily_goal_history") if isinstance(handoff, dict) else []
        priority = _clamp_int(focus.get("priority") or 3, 1, 5)
        score = priority * 10 - int(focus.get("focus_order") or 1)
        reasons = [f"priority={priority}"]

        if latest_focus_id == focus.get("id") and status_after_checkin not in {"completed", "blocked"}:
            score += 12
            reasons.append("延续昨天未完成 focus")
        if today.isoweekday() == 1 and not history:
            score += 6
            reasons.append("周一优先启动新 focus")
        if today.isoweekday() == 5 and _schema_goal_type(focus.get("focus_type")) in {
            "testing",
            "review",
            "documentation",
        }:
            score += 5
            reasons.append("周五偏验证或收口")

        direction_bonus = _direction_alignment_score(tomorrow_direction, focus)
        if direction_bonus:
            score += direction_bonus
            reasons.append("明天方向与该 focus 可桥接")

        progress_score = _float_or_zero(handoff.get("progress_score"))
        if progress_score >= 0.95:
            score -= 25
            reasons.append("该 focus 已接近完成")
        if status_after_checkin == "blocked":
            score -= 25
            reasons.append("该 focus 上次被阻塞")

        scored.append((score, focus, "；".join(reasons)))

    if not scored:
        return None, "本周 weekly_focus 均已完成，回退到用户画像和近期记录生成。"

    scored.sort(key=lambda item: item[0], reverse=True)
    score, selected, reason = scored[0]
    focus_text = _compact_text(selected.get("focus_text") or "weekly_focus", 60)
    return selected, f"选择 `{focus_text}`：{reason}；得分 {score}。"


def _latest_selected_focus_id(recent_goals: list[dict[str, Any]]) -> int | None:
    for record in recent_goals:
        snapshot = (record.get("daily_goal") or {}).get("context_snapshot") or {}
        focus_id = snapshot.get("selected_weekly_focus_id")
        if focus_id not in (None, ""):
            try:
                return int(focus_id)
            except (TypeError, ValueError):
                return None
    return None


def _direction_alignment_score(tomorrow_direction: str | None, focus: dict[str, Any]) -> int:
    direction = str(tomorrow_direction or "").strip().lower()
    if not direction:
        return 0
    focus_text = f"{focus.get('focus_text') or ''} {focus.get('desired_outcome') or ''}".lower()
    if direction and direction in focus_text:
        return 12
    focus_type = _schema_goal_type(focus.get("focus_type"))
    type_tokens = {
        "testing": ["测试", "test", "评估", "eval"],
        "documentation": ["文档", "周报", "报告", "docs"],
        "design": ["页面", "前端", "展示", "design"],
        "debugging": ["修复", "调试", "bug"],
        "review": ["复盘", "review", "收口"],
        "coding": ["代码", "实现", "接口", "后端"],
    }
    if any(token in direction for token in type_tokens.get(focus_type, [])):
        return 8
    if any(token in direction for token in ("分钟", "小时", "时间", "只有")):
        return 4
    return 0


def _build_focus_deviation_log(
    today: date,
    selected_focus: dict[str, Any] | None,
    tomorrow_direction: str | None,
    reason: str,
) -> dict[str, Any] | None:
    direction = str(tomorrow_direction or "").strip()
    if not selected_focus or not direction:
        return None
    alignment = _direction_alignment_score(direction, selected_focus)
    conflict_level = "no_conflict" if alignment >= 8 else "soft_conflict"
    return {
        "date": today.isoformat(),
        "tomorrow_direction": direction,
        "conflict_level": conflict_level,
        "decision": "bridge_to_weekly_focus",
        "selected_focus_item_id": selected_focus["id"],
        "reason": reason,
    }


def _mark_selected_focus_carried(
    connection: sqlite3.Connection,
    context: dict[str, Any],
    daily_goal_id: int,
    goal_output: dict[str, Any],
) -> None:
    selected_focus = context.get("selected_weekly_focus")
    if not selected_focus:
        return

    weekly_focus = repo.get_weekly_focus(connection, int(selected_focus["id"]))
    if weekly_focus is None:
        return

    payload = dict(weekly_focus.get("context_payload") or {})
    handoff = dict(payload.get("handoff") or {})
    history = list(handoff.get("daily_goal_history") or [])
    if not any(int(item.get("daily_goal_id") or 0) == int(daily_goal_id) for item in history):
        history.append(
            {
                "daily_goal_id": daily_goal_id,
                "goal_date": context["goal_date"],
                "main_goal": goal_output["main_goal"],
                "selection_reason": context.get("focus_selection_reason"),
            }
        )
    handoff.update(
        {
            "selected_on_date": context["goal_date"],
            "selected_daily_goal_id": daily_goal_id,
            "focus_selection_reason": context.get("focus_selection_reason"),
            "status_after_selection": "in_progress",
            "daily_goal_history": history[-5:],
        }
    )
    payload["handoff"] = handoff

    changes: dict[str, Any] = {"context_payload": payload}
    if weekly_focus.get("carried_into_goal_id") in (None, ""):
        changes["carried_into_goal_id"] = daily_goal_id
    repo.update_weekly_focus(connection, int(weekly_focus["id"]), **changes)


def _select_goal_type(tomorrow_direction: str, weekly_focus: list[dict[str, Any]]) -> str:
    focus_type = weekly_focus[0].get("focus_type") if weekly_focus else None
    text = f"{tomorrow_direction} {focus_type or ''}".lower()
    if any(token in text for token in ("测试", "test")):
        return "testing"
    if any(token in text for token in ("文档", "docs", "documentation")):
        return "documentation"
    if any(token in text for token in ("设计", "design")):
        return "design"
    if any(token in text for token in ("调试", "修复", "debug", "bug")):
        return "debugging"
    if any(token in text for token in ("复盘", "review")):
        return "review"
    if any(token in text for token in ("规划", "计划", "plan")):
        return "planning"
    if any(token in text for token in ("研究", "research")):
        return "research"
    return "coding"


def _schema_goal_type(goal_type: Any) -> str:
    mapping = {
        "implementation": "coding",
        "code": "coding",
        "docs": "documentation",
        "document": "documentation",
    }
    value = str(goal_type or "coding")
    value = mapping.get(value, value)
    allowed = {
        "design",
        "coding",
        "documentation",
        "review",
        "learning",
        "research",
        "debugging",
        "testing",
        "planning",
    }
    return value if value in allowed else "coding"


def _growth_tags_from_record(active_version: dict[str, Any]) -> list[str]:
    goal_type = _schema_goal_type(active_version.get("goal_type"))
    tags = ["daypilot_mvp", "daily_goal", goal_type]
    return list(dict.fromkeys(tags))


def _ensure_string_list(value: Any, *, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                items = [str(item).strip() for item in decoded if str(item).strip()]
            else:
                items = [stripped]
        elif stripped:
            items = [part.strip() for part in stripped.replace("；", ";").split(";") if part.strip()]
        else:
            items = []
    else:
        items = []

    if len(items) < len(fallback):
        items.extend(fallback[len(items) :])
    return items


def _first_project(profile: dict[str, Any]) -> str | None:
    projects = profile.get("current_focus_projects")
    if isinstance(projects, list) and projects:
        return str(projects[0])
    return None


def _first_project_from_projects(projects: list[dict[str, Any]]) -> str | None:
    for project in projects:
        if str(project.get("status") or "active") != "active":
            continue
        name = str(project.get("name") or "").strip()
        if name:
            return name
    return None


def _looks_like_large_direction(text: str) -> bool:
    return any(token in text for token in ("全部", "完整", "端到端", "all", "complete", "entire"))


def _daily_focus_slice_text(focus: dict[str, Any]) -> str:
    text = str(focus.get("focus_text") or focus.get("desired_outcome") or "DayPilot MVP").strip()
    for token in ("下周", "本周", "整周", "完整", "全部", "整个", "端到端", "长期"):
        text = text.replace(token, "")
    text = text.strip(" ，；。")
    return _compact_text(text or "DayPilot MVP", 52)


def _tomorrow_handling_for_focus(tomorrow_direction: str) -> str:
    if not tomorrow_direction:
        return "empty_agent_decided"
    if any(token in tomorrow_direction for token in ("分钟", "小时", "时间", "只有")):
        return "narrowed_to_daily_scope"
    return "partially_used"


def _json_safe_context(context: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(context, ensure_ascii=False, default=str))


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_text(value: Any, max_chars: int) -> str:
    return " ".join(str(value).split())[:max_chars]


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
