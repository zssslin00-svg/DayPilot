from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.soul_context import SOUL_PATH
from backend.services.soul_frontend_sync_service import sync_career_recommendation_to_soul
from backend.services.today_goal_service import goal_output_from_record
from backend.services.workday_policy import is_workday


@dataclass(frozen=True)
class CareerRecommendationAdoptionResult:
    payload: dict[str, Any]


class CareerRecommendationValidationError(ValueError):
    """Raised when a career recommendation adoption request is invalid."""


def adopt_career_recommendation(
    db_path: str | Path = DEFAULT_DB_PATH,
    request_body: dict[str, Any] | None = None,
    *,
    today: date,
    soul_path: str | Path = SOUL_PATH,
) -> CareerRecommendationAdoptionResult:
    body = dict(request_body or {})
    message_id = _positive_int(body.get("message_id"), "message_id")
    recommendation_index = _non_negative_int(body.get("recommendation_index"), "recommendation_index")
    mode = str(body.get("mode") or "auto").strip()
    if mode not in {"auto", "existing_project", "new_project"}:
        raise CareerRecommendationValidationError("mode must be auto, existing_project, or new_project.")
    requested_project_id = _optional_positive_int(body.get("project_id"), "project_id")

    connection = initialize_database(db_path)
    try:
        with connection:
            message = _assistant_message_with_recommendation(connection, message_id, recommendation_index)
            recommendation = message["recommendations"][recommendation_index]
            existing_action = repo.get_career_recommendation_action_by_source(
                connection,
                message_id,
                recommendation_index,
            )
            if existing_action is not None:
                return CareerRecommendationAdoptionResult(
                    _existing_action_payload(connection, existing_action, today, recommendation)
                )

            project_resolution = _resolve_project_for_recommendation(
                connection,
                recommendation,
                mode=mode,
                requested_project_id=requested_project_id,
            )
            if project_resolution["status"] == "needs_project_choice":
                return CareerRecommendationAdoptionResult(
                    {
                        "status": "needs_project_choice",
                        "message": "这个建议可能属于多个当前项目，请选择一个项目或建为新项目。",
                        "candidates": project_resolution["candidates"],
                    }
                )

            project = project_resolution["project"]
            if project_resolution["project_changed"]:
                _sync_user_profile_projects(connection)

            goal_payload = None
            status = "pending_next_workday"
            if is_workday(today):
                goal_payload = _create_recommendation_goal(
                    connection,
                    today,
                    project,
                    recommendation,
                    message_id=message_id,
                    recommendation_index=recommendation_index,
                )
                status = "applied"

            action_id = repo.create_career_recommendation_action(
                connection,
                session_id=int(message["session_id"]),
                message_id=message_id,
                recommendation_index=recommendation_index,
                status=status,
                action=project_resolution["action"],
                project_id=int(project["id"]),
                daily_goal_id=goal_payload["daily_goal"]["id"] if goal_payload else None,
                recommendation_snapshot=recommendation,
                source_payload={
                    "source": "career_chat_recommendation",
                    "mode": mode,
                    "project_resolution": project_resolution["reason"],
                    "project_binding": _project_binding(recommendation),
                    "created_on_workday": is_workday(today),
                },
            )
            action = repo.get_career_recommendation_action(connection, action_id)
            project = repo.get_project(connection, int(project["id"]))

        soul_sync = sync_career_recommendation_to_soul(
            db_path,
            action_id=int(action["id"]),
            allow_current_project_append=project_resolution["action"] in {"new_project_goal", "restored_project_goal"}
            and bool(project_resolution["project_changed"]),
            today=today,
            soul_path=soul_path,
        )
        return CareerRecommendationAdoptionResult(
            _adoption_payload(
                action=action,
                project=project,
                goal=goal_payload,
                recommendation=recommendation,
                already_applied=False,
                soul_sync=soul_sync,
                today=today,
            )
        )
    except sqlite3.DatabaseError as exc:
        raise CareerRecommendationValidationError(str(exc)) from exc
    finally:
        connection.close()


def attach_recommendation_actions(
    connection,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant" or not isinstance(message.get("recommendations"), list):
            result.append(message)
            continue
        actions = {
            int(action["recommendation_index"]): action
            for action in repo.list_career_recommendation_actions_for_message(connection, int(message["id"]))
        }
        recommendations = []
        for index, recommendation in enumerate(message.get("recommendations") or []):
            item = dict(recommendation)
            action = actions.get(index)
            if action is not None:
                project = repo.get_project(connection, int(action["project_id"]))
                item["adoption"] = _public_action(action, project=project)
            recommendations.append(item)
        enriched = dict(message)
        enriched["recommendations"] = recommendations
        result.append(enriched)
    return result


def _assistant_message_with_recommendation(
    connection,
    message_id: int,
    recommendation_index: int,
) -> dict[str, Any]:
    message = repo.get_career_chat_message(connection, message_id)
    if message is None or message.get("role") != "assistant":
        raise CareerRecommendationValidationError("message_id must refer to an assistant career message.")
    recommendations = message.get("recommendations")
    if not isinstance(recommendations, list):
        raise CareerRecommendationValidationError("assistant message does not contain recommendations.")
    if recommendation_index >= len(recommendations):
        raise CareerRecommendationValidationError("recommendation_index is out of range.")
    if not isinstance(recommendations[recommendation_index], dict):
        raise CareerRecommendationValidationError("recommendation must be an object.")
    return message


def _existing_action_payload(
    connection,
    action: dict[str, Any],
    today: date,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    goal = _goal_payload_for_action(connection, action)
    if goal is None and action["status"] == "pending_next_workday" and is_workday(today):
        project = repo.get_project(connection, int(action["project_id"]))
        if project is None:
            raise CareerRecommendationValidationError("adopted project was not found.")
        goal = _create_recommendation_goal(
            connection,
            today,
            project,
            recommendation,
            message_id=int(action["message_id"]),
            recommendation_index=int(action["recommendation_index"]),
        )
        action = repo.update_career_recommendation_action(
            connection,
            int(action["id"]),
            status="applied",
            daily_goal_id=int(goal["daily_goal"]["id"]),
        ) or action
    project = repo.get_project(connection, int(action["project_id"]))
    return _adoption_payload(
        action=action,
        project=project,
        goal=goal,
        recommendation=recommendation,
        already_applied=True,
        soul_sync={"status": "not_applicable"},
        today=today,
    )


def _resolve_project_for_recommendation(
    connection,
    recommendation: dict[str, Any],
    *,
    mode: str,
    requested_project_id: int | None,
) -> dict[str, Any]:
    active_projects = repo.list_projects(connection)
    if mode == "existing_project":
        if requested_project_id is None:
            raise CareerRecommendationValidationError("project_id is required for existing_project mode.")
        project = repo.get_project(connection, requested_project_id)
        if project is None or str(project.get("status") or "") != "active":
            raise CareerRecommendationValidationError("project_id must refer to an active project.")
        return {
            "status": "resolved",
            "project": project,
            "action": "existing_project_goal",
            "project_changed": False,
            "reason": "user_selected_existing_project",
        }

    if mode == "auto":
        binding_resolution = _resolve_bound_project(connection, active_projects, recommendation)
        if binding_resolution is not None:
            return binding_resolution
        matches = _matching_projects(active_projects, recommendation)
        if len(matches) > 1:
            return {
                "status": "needs_project_choice",
                "candidates": [_project_candidate(item) for item in matches],
            }
        if len(matches) == 1:
            return {
                "status": "resolved",
                "project": matches[0],
                "action": "existing_project_goal",
                "project_changed": False,
                "reason": "matched_active_project_name",
            }

    return _create_or_restore_project(connection, recommendation)


def _resolve_bound_project(
    connection,
    active_projects: list[dict[str, Any]],
    recommendation: dict[str, Any],
) -> dict[str, Any] | None:
    binding = _project_binding(recommendation)
    if binding is None:
        return None

    project_name = binding["project_name"]
    active_by_name = {_project_name_key(project.get("name")): project for project in active_projects}
    project_key = _project_name_key(project_name)
    if binding["kind"] not in {"existing_project", "new_project"}:
        return {
            "status": "needs_project_choice",
            "candidates": [_project_candidate(item) for item in active_projects],
            "reason": "invalid_project_binding",
        }
    if binding["kind"] == "existing_project":
        project = active_by_name.get(project_key)
        if project is not None:
            return {
                "status": "resolved",
                "project": project,
                "action": "existing_project_goal",
                "project_changed": False,
                "reason": "bound_existing_project_name",
            }
        return {
            "status": "needs_project_choice",
            "candidates": [_project_candidate(item) for item in active_projects],
            "reason": "invalid_bound_existing_project_name",
        }

    if project_key in active_by_name:
        project = active_by_name[project_key]
        return {
            "status": "resolved",
            "project": project,
            "action": "existing_project_goal",
            "project_changed": False,
            "reason": "bound_new_project_reused_matching_active_project",
        }
    return _create_or_restore_project(connection, recommendation)


def _project_binding(recommendation: dict[str, Any]) -> dict[str, str] | None:
    binding = recommendation.get("project_binding")
    if not isinstance(binding, dict):
        return None
    kind = str(binding.get("kind") or "").strip()
    project_name = _compact_text(binding.get("project_name"), 90)
    if kind not in {"existing_project", "new_project"} or len(project_name) < 2:
        return {
            "kind": "invalid",
            "project_name": project_name,
        }
    reason = _compact_text(binding.get("reason"), 180)
    normalized = {
        "kind": kind,
        "project_name": project_name,
    }
    if reason:
        normalized["reason"] = reason
    return normalized


def _project_name_for_new_project(recommendation: dict[str, Any]) -> str:
    binding = _project_binding(recommendation)
    if binding is not None and binding.get("project_name") and binding.get("kind") == "new_project":
        return _compact_text(binding["project_name"], 90)
    return _compact_text(recommendation.get("title") or "职业规划成长项目", 90)


def _create_or_restore_project(connection, recommendation: dict[str, Any]) -> dict[str, Any]:
    project_name = _project_name_for_new_project(recommendation)
    existing = _find_project_by_name_key(connection, project_name, include_archived=True)
    summary = _compact_text(recommendation.get("why_it_fits") or "来自职业规划建议，适合作为可交付成长实验。", 180)
    target_goal = _compact_text(recommendation.get("deliverable") or project_name, 160)
    planning_guidance = _compact_text(
        f"优先保持最小可交付；第一步：{recommendation.get('first_step') or target_goal}",
        220,
    )
    source_payload = {
        "source": "career_recommendation",
        "target_goal": target_goal,
        "today_goal": "",
        "project_state_patch": {
            "summary": summary,
            "planning_guidance": planning_guidance,
            "target_goal": target_goal,
            "today_goal": "",
            "facts": [
                {
                    "type": "context",
                    "text": _compact_text(f"职业规划建议：{project_name}", 120),
                },
                {
                    "type": "artifact",
                    "text": target_goal,
                },
            ],
        },
    }
    if existing is not None:
        updated = repo.update_project(
            connection,
            int(existing["id"]),
            status="active",
            project_state=repo.merge_project_state(
                existing.get("project_state"),
                source_payload["project_state_patch"],
                updated_from={"source": "career_recommendation_restore"},
            ),
        )
        return {
            "status": "resolved",
            "project": updated or existing,
            "action": "restored_project_goal" if existing.get("status") != "active" else "existing_project_goal",
            "project_changed": existing.get("status") != "active",
            "reason": "restored_or_reused_project_by_title",
        }

    project_id = repo.create_project(
        connection,
        name=project_name,
        priority="P2",
        role="support",
        status="active",
        status_summary=summary,
        planning_bias=planning_guidance,
        source_payload=source_payload,
    )
    project = repo.get_project(connection, project_id)
    return {
        "status": "resolved",
        "project": project,
        "action": "new_project_goal",
        "project_changed": True,
        "reason": "created_project_from_recommendation",
    }


def _create_recommendation_goal(
    connection,
    today: date,
    project: dict[str, Any],
    recommendation: dict[str, Any],
    *,
    message_id: int,
    recommendation_index: int,
) -> dict[str, Any]:
    goal_date = today.isoformat()
    project_id = int(project["id"])
    goal_output = _goal_output_from_recommendation(today, project, recommendation)
    source_payload = {
        "source": "career_chat_recommendation",
        "message_id": message_id,
        "recommendation_index": recommendation_index,
    }
    context_snapshot = {
        "schema_version": "career_recommendation_goal_context.v1",
        "project_id": project_id,
        "project_name": project["name"],
        "message_id": message_id,
        "recommendation_index": recommendation_index,
        "recommendation_title": recommendation.get("title"),
        "goal_output_context_used": goal_output["context_used"],
    }
    existing_goal = _open_daily_goal_for_project_date(connection, goal_date, project_id)
    if existing_goal is not None:
        daily_goal_id = int(existing_goal["id"])
        merged_source_payload = dict(existing_goal.get("source_payload") or {})
        merged_source_payload["latest_career_recommendation"] = source_payload
        repo.update_daily_goal(
            connection,
            daily_goal_id,
            source_payload=merged_source_payload,
            context_snapshot={**dict(existing_goal.get("context_snapshot") or {}), **context_snapshot},
            generated_at=_now_text(),
            status="active",
        )
        revision_source = "system_regeneration" if existing_goal.get("active_version_id") is not None else "initial_generation"
        revision_reason = "Career recommendation adopted into the existing project daily goal."
    else:
        display_order = repo.next_daily_goal_display_order(connection, goal_date, project_id)
        daily_goal_id = repo.create_daily_goal(
            connection,
            project_id=project_id,
            goal_date=goal_date,
            goal_source="career_recommendation",
            source_payload=source_payload,
            context_snapshot=context_snapshot,
            generated_at=_now_text(),
            display_order=display_order,
        )
        revision_source = "initial_generation"
        revision_reason = "Career recommendation adopted as a project daily goal."
    version_id = repo.create_goal_version(
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
        avoid_today=json.dumps(goal_output["do_not_do_today"], ensure_ascii=False, separators=(",", ":")),
        goal_type=goal_output["goal_type"],
        revision_source=revision_source,
        revision_reason=revision_reason,
        critic_result={
            "schema": "daily_goal.v1",
            "quality_status": "accepted",
            "source": "career_recommendation",
        },
        prompt_version="career_recommendation_goal_v1",
    )
    daily_goal = repo.get_daily_goal(connection, daily_goal_id)
    active_version = repo.get_goal_version(connection, version_id)
    record = {
        "daily_goal": daily_goal,
        "project": repo.get_project(connection, project_id),
        "active_version": active_version,
        "daily_checkin": repo.get_daily_checkin_for_goal(connection, daily_goal_id),
    }
    return {**record, "goal_output": goal_output_from_record(record)}


def _goal_output_from_recommendation(
    today: date,
    project: dict[str, Any],
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    project_name = str(project.get("name") or "当前项目").strip()
    title = _compact_text(recommendation.get("title") or "职业规划成长项目", 90)
    deliverable = _compact_text(recommendation.get("deliverable") or "一份可检查的项目记录或实验结果", 150)
    first_step = _compact_text(recommendation.get("first_step") or deliverable, 160)
    risks = _compact_text(recommendation.get("risks") or "主要风险是范围过大，需要保持最小可交付。", 180)
    not_now = _compact_text(recommendation.get("not_now_reason") or "如果时间不足，先完成最小记录。", 180)
    estimated_minutes = _estimated_minutes(recommendation.get("estimated_time"))
    return {
        "schema_version": "daily_goal.v1",
        "goal_date": today.isoformat(),
        "main_goal": _compact_text(f"围绕「{project_name}」完成职业建议：{title}", 120),
        "rationale": _compact_text(
            recommendation.get("why_it_fits")
            or f"该目标来自职业规划建议，适合作为「{project_name}」的额外可验证成长实验。",
            260,
        ),
        "completion_criteria": [
            _compact_text(f"完成可交付物：{deliverable}", 140),
            _compact_text(f"推进第一步：{first_step}", 140),
            "记录结果、验证情况或下一步限制，便于后续复盘。",
        ],
        "estimated_minutes": estimated_minutes,
        "difficulty": 3 if estimated_minutes >= 90 else 2,
        "minimum_acceptable_result": _compact_text(f"至少留下可复查记录：{deliverable}", 220),
        "stretch_challenge": "如果最低成果完成，补充一条复盘结论或下一步实验边界。",
        "do_not_do_today": [
            _compact_text(f"不要扩展到建议卡以外的完整项目范围；风险：{risks}", 160),
            _compact_text(f"保留不现在做的边界：{not_now}", 160),
        ],
        "goal_type": "research",
        "growth_tags": _string_list(recommendation.get("skills_to_build"), ["职业成长", "项目实验"], max_items=4),
        "context_used": {
            "primary_driver": "career_recommendation",
            "tomorrow_direction_handling": "not_applicable",
            "continuity_note": "这是一条由职业规划推荐卡创建的附加今日目标，不覆盖项目原有今日目标。",
            "difficulty_reason": "难度和时间来自推荐卡的预计时间，并限制在当天可执行范围内。",
        },
    }


def _goal_payload_for_action(connection, action: dict[str, Any]) -> dict[str, Any] | None:
    daily_goal_id = action.get("daily_goal_id")
    if daily_goal_id is None:
        return None
    daily_goal = repo.get_daily_goal(connection, int(daily_goal_id))
    if daily_goal is None:
        return None
    record = {
        "daily_goal": daily_goal,
        "project": repo.get_project(connection, int(daily_goal["project_id"])),
        "active_version": (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal.get("active_version_id") is not None
            else None
        ),
        "daily_checkin": repo.get_daily_checkin_for_goal(connection, int(daily_goal["id"])),
    }
    return {**record, "goal_output": goal_output_from_record(record)}


def _adoption_payload(
    *,
    action: dict[str, Any] | None,
    project: dict[str, Any] | None,
    goal: dict[str, Any] | None,
    recommendation: dict[str, Any],
    already_applied: bool,
    soul_sync: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    public_action = _public_action(action, project=project) if action is not None else None
    status = "already_applied" if already_applied else (action or {}).get("status", "applied")
    return {
        "status": status,
        "already_applied": already_applied,
        "action": public_action,
        "project": project,
        "goal": goal,
        "recommendation": recommendation,
        "is_workday": is_workday(today),
        "soul_sync": soul_sync,
        "message": _payload_message(status, project, goal, today),
    }


def _public_action(action: dict[str, Any], *, project: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": action["id"],
        "status": action["status"],
        "action": action["action"],
        "project_id": action["project_id"],
        "project_name": project.get("name") if project else None,
        "daily_goal_id": action.get("daily_goal_id"),
        "recommendation_index": action["recommendation_index"],
        "created_at": action["created_at"],
        "updated_at": action["updated_at"],
    }


def _payload_message(
    status: str,
    project: dict[str, Any] | None,
    goal: dict[str, Any] | None,
    today: date,
) -> str:
    project_name = project.get("name") if project else "当前项目"
    if status == "already_applied":
        return f"这个建议已经加入「{project_name}」。"
    if goal is not None:
        return f"已为「{project_name}」新增一条职业建议今日目标。"
    if not is_workday(today):
        return f"已记录建议并加入「{project_name}」，下个工作日可承接。"
    return f"已记录建议并加入「{project_name}」。"


def _find_project_by_name_key(
    connection,
    name: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    key = _project_name_key(name)
    if not key:
        return None
    for project in repo.list_projects(connection, include_archived=include_archived):
        if _project_name_key(project.get("name")) == key:
            return project
    return None


def _open_daily_goal_for_project_date(
    connection,
    goal_date: str,
    project_id: int,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for goal in repo.list_daily_goals_by_date(connection, goal_date):
        if int(goal.get("project_id") or 0) != int(project_id):
            continue
        if str(goal.get("status") or "active") != "active":
            continue
        if repo.get_daily_checkin_for_goal(connection, int(goal["id"])) is not None:
            continue
        candidates.append(goal)
    if not candidates:
        return None
    return sorted(candidates, key=_open_daily_goal_rank)[0]


def _open_daily_goal_rank(goal: dict[str, Any]) -> tuple[int, int, int]:
    source_rank = 0 if str(goal.get("goal_source") or "") == "daily_planning" else 1
    return (source_rank, int(goal.get("display_order") or 0), int(goal.get("id") or 0))


def _matching_projects(projects: list[dict[str, Any]], recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    raw_text = " ".join(
        str(recommendation.get(key) or "")
        for key in ("title", "why_it_fits", "deliverable", "first_step")
    )
    text_key = _project_name_key(raw_text)
    normalized_matches = [
        project
        for project in projects
        if _project_name_key(project.get("name")) and _project_name_key(project.get("name")) in text_key
    ]
    if normalized_matches:
        return normalized_matches

    text = _normalize_match_text(
        " ".join(
            str(recommendation.get(key) or "")
            for key in ("title", "why_it_fits", "deliverable", "first_step")
        )
    )
    matches = []
    for project in projects:
        name = str(project.get("name") or "").strip()
        key = _normalize_match_text(name)
        if not key:
            continue
        if key in text:
            matches.append(project)
            continue
        tokens = [token for token in re.split(r"[\s/_\-:：()（）]+", key) if len(token) >= 3]
        if tokens and any(token in text for token in tokens):
            matches.append(project)
    return matches


def _project_candidate(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project["id"],
        "name": project["name"],
        "status_summary": project.get("status_summary") or "",
        "today_goal": repo.project_today_goal(project),
    }


def _sync_user_profile_projects(connection) -> None:
    active_projects = repo.list_projects(connection)
    profile = repo.get_user_profile(connection)
    project_priorities = [
        {
            "id": project["id"],
            "name": project["name"],
            "priority": project["priority"],
            "role": project.get("role") or "",
            "progress": project.get("status_summary") or "",
            "planning_bias": project.get("planning_bias") or "",
            "target_goal": repo.project_target_goal(project),
            "today_goal": repo.project_today_goal(project),
        }
        for project in active_projects
    ]
    if profile is None:
        repo.create_user_profile(
            connection,
            id=1,
            long_term_direction="项目状态由职业规划建议初始化。",
            current_focus_projects=[project["name"] for project in active_projects],
            goal_preferences={"project_priorities": project_priorities},
        )
        return
    preferences = dict(profile.get("goal_preferences") or {})
    preferences["project_priorities"] = project_priorities
    repo.update_user_profile(
        connection,
        int(profile["id"]),
        current_focus_projects=[project["name"] for project in active_projects],
        goal_preferences=preferences,
    )


def _positive_int(value: Any, field_name: str) -> int:
    parsed = _optional_positive_int(value, field_name)
    if parsed is None:
        raise CareerRecommendationValidationError(f"{field_name} is required.")
    return parsed


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CareerRecommendationValidationError(f"{field_name} must be an integer.") from exc
    if parsed <= 0:
        raise CareerRecommendationValidationError(f"{field_name} must be positive.")
    return parsed


def _non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CareerRecommendationValidationError(f"{field_name} must be an integer.") from exc
    if parsed < 0:
        raise CareerRecommendationValidationError(f"{field_name} must be non-negative.")
    return parsed


def _estimated_minutes(value: Any) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    if not numbers:
        return 60
    if len(numbers) >= 2:
        minutes = round((numbers[0] + numbers[1]) / 2)
    else:
        minutes = numbers[0]
    return max(30, min(150, minutes))


def _string_list(value: Any, fallback: list[str], *, max_items: int) -> list[str]:
    raw = value if isinstance(value, list) else []
    items = [_compact_text(item, 60) for item in raw if _compact_text(item, 60)]
    return (items or fallback)[:max_items]


def _compact_text(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _project_name_key(value: Any) -> str:
    return re.sub(r"[\s/_\-:：,，.。;；、()（）\[\]【】《》<>]+", "", str(value or "").casefold()).strip()


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().replace(microsecond=0).isoformat(sep=" ")
