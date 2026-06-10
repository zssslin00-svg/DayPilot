from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.services.llm_client import generate_json_with_fallback


PROMPT_VERSION_MOCK = "project_progress_v1_mock"
PROMPT_VERSION_DEEPSEEK = "project_progress_v1_deepseek"
MOCK_MODEL_NAME = "mock-project-progress-adapter"


@dataclass(frozen=True)
class ProjectProgressUpdateResult:
    payload: dict[str, Any]


class ProjectProgressUpdateError(RuntimeError):
    """Raised when project progress cannot be analyzed or persisted."""


def ensure_projects_seeded(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    projects = repo.list_projects(connection, include_archived=True)
    profile = repo.get_user_profile(connection)
    if profile is None:
        return repo.list_projects(connection)

    preferences = profile.get("goal_preferences") or {}
    priority_items = preferences.get("project_priorities")
    current_projects = profile.get("current_focus_projects")
    has_profile_projects = (
        isinstance(priority_items, list) and bool(priority_items)
    ) or (isinstance(current_projects, list) and bool(current_projects))
    if projects and not (_only_auto_default_projects(projects) and has_profile_projects):
        return repo.list_projects(connection)
    auto_default_by_id: dict[int, dict[str, Any]] = {}
    if projects and _only_auto_default_projects(projects) and has_profile_projects:
        auto_default_by_id = {int(project["id"]): project for project in projects}
        for project in projects:
            repo.update_project(connection, int(project["id"]), status="archived")

    if isinstance(priority_items, list) and priority_items:
        used_project_ids: set[int] = set()
        for index, item in enumerate(priority_items, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            project_id = _safe_positive_int(item.get("id")) or index
            project_data = {
                "name": name,
                "priority": _valid_priority(item.get("priority")),
                "role": str(item.get("role") or "").strip(),
                "status": "active",
                "status_summary": str(item.get("progress") or "").strip(),
                "planning_bias": str(item.get("planning_bias") or "").strip(),
                "source_payload": item,
            }
            if project_id in auto_default_by_id and project_id not in used_project_ids:
                repo.update_project(connection, project_id, **project_data)
                used_project_ids.add(project_id)
                continue
            if repo.get_project(connection, project_id) is None and project_id not in used_project_ids:
                repo.create_project(connection, id=project_id, **project_data)
                used_project_ids.add(project_id)
                continue
            repo.create_project(connection, **project_data)
        return repo.list_projects(connection)

    if isinstance(current_projects, list):
        for index, name in enumerate(current_projects, start=1):
            project_name = str(name or "").strip()
            if not project_name:
                continue
            repo.create_project(
                connection,
                name=project_name,
                priority="P2",
                role="active",
                status="active",
                status_summary="",
                planning_bias="",
                source_payload={"source": "user_profile.current_focus_projects"},
            )
    return repo.list_projects(connection)


def _only_auto_default_projects(projects: list[dict[str, Any]]) -> bool:
    if not projects:
        return False
    return all(_is_auto_default_project(project) for project in projects)


def _is_auto_default_project(project: dict[str, Any]) -> bool:
    payload = project.get("source_payload") or {}
    source = payload.get("source") if isinstance(payload, dict) else None
    if source in {"repository_default", "schema_migration"}:
        return True
    return str(project.get("name") or "") == "DayPilot 默认项目" and source in {
        "repository_default",
        "schema_migration",
    }


def update_project_progress_for_checkin(
    db_path: str | Path,
    checkin_id: int,
    *,
    settings: DayPilotSettings | None = None,
) -> ProjectProgressUpdateResult:
    try:
        context = _build_progress_context(db_path, checkin_id)
        projects = context["projects"]
        if not projects:
            return ProjectProgressUpdateResult(
                {
                    "status": "skipped",
                    "reason": "no_projects_configured",
                }
            )

        valid_project_ids = {int(project["id"]) for project in projects}
        llm_result = generate_json_with_fallback(
            task_name="project_progress_update",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda soul: _progress_messages(context, soul),
            mock_generate=lambda: MockProjectProgressLLMAdapter().generate(context),
            validator=lambda output: _validate_progress_output(output, valid_project_ids),
            settings=settings,
        )
        output = _normalize_progress_output(llm_result.output)
        project_id = int(output["project_id"])
        event_id, project = _persist_progress_update(
            db_path,
            checkin_id=checkin_id,
            output=output,
            llm_metadata=llm_result.metadata,
        )
        return ProjectProgressUpdateResult(
            {
                "status": "updated",
                "project_id": project_id,
                "project_name": project["name"],
                "project_progress_event_id": event_id,
                "confidence": output["confidence"],
                "reason": output.get("reason"),
                "fallback_reason": llm_result.metadata.get("fallback_reason"),
                "llm_mode_used": llm_result.metadata.get("llm_mode_used"),
                "model_name": llm_result.metadata.get("model_name"),
            }
        )
    except Exception as exc:  # noqa: BLE001 - check-in persistence must not be rolled back
        return ProjectProgressUpdateResult(
            {
                "status": "failed",
                "reason": _safe_error(exc),
            }
        )


class MockProjectProgressLLMAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        projects = context["projects"]
        checkin_text = _checkin_text(context)
        daily_goal_project = context.get("daily_goal_project") or {}
        active_project_ids = {int(project["id"]) for project in projects}
        default_project = (
            daily_goal_project
            if daily_goal_project and int(daily_goal_project.get("id") or 0) in active_project_ids
            else None
        )
        selected = _select_project_by_text(projects, checkin_text) or default_project or projects[0]
        progress_delta = _compact_sentence(
            context["checkin"].get("completion_text")
            or context["active_version"].get("main_goal")
            or selected["name"],
            110,
        )
        current_summary = str(selected.get("status_summary") or "").strip()
        return {
            "project_id": selected["id"],
            "confidence": 0.62,
            "progress_delta": progress_delta,
            "new_status_summary": _merge_summary(current_summary, progress_delta),
            "evidence_text": _compact_sentence(context["checkin"].get("completion_text") or "", 160),
            "reason": "mock fallback selected the closest project from available check-in context.",
        }


def _build_progress_context(db_path: str | Path, checkin_id: int) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        with connection:
            ensure_projects_seeded(connection)
        checkin = repo.get_daily_checkin(connection, checkin_id)
        if checkin is None:
            raise ProjectProgressUpdateError("checkin_not_found")
        daily_goal = repo.get_daily_goal(connection, int(checkin["daily_goal_id"]))
        if daily_goal is None:
            raise ProjectProgressUpdateError("daily_goal_not_found")
        daily_goal_project = repo.get_project(connection, int(daily_goal["project_id"]))
        active_version = (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal.get("active_version_id") is not None
            else None
        )
        projects = repo.list_projects(connection)
        active_project_ids = {int(project["id"]) for project in projects}
        if daily_goal_project is not None and int(daily_goal_project["id"]) not in active_project_ids:
            daily_goal_project = None
        recent_events = repo.list_recent_project_progress_events(connection, limit=10)
        return {
            "checkin": checkin,
            "daily_goal": daily_goal,
            "daily_goal_project": daily_goal_project,
            "active_version": active_version or {},
            "projects": projects,
            "recent_project_progress_events": recent_events,
        }
    finally:
        connection.close()


def _persist_progress_update(
    db_path: str | Path,
    *,
    checkin_id: int,
    output: dict[str, Any],
    llm_metadata: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    connection = initialize_database(db_path)
    try:
        with connection:
            ensure_projects_seeded(connection)
            old_events = repo.list_project_progress_events_for_source(
                connection,
                "daily_checkin",
                checkin_id,
                active_only=True,
            )
            _restore_superseded_project_summaries(connection, old_events)
            repo.supersede_project_progress_events_for_source(
                connection,
                "daily_checkin",
                checkin_id,
            )

            project = repo.get_project(connection, int(output["project_id"]))
            if project is None:
                raise ProjectProgressUpdateError("project_not_found")
            checkin = repo.get_daily_checkin(connection, checkin_id)
            if checkin is None:
                raise ProjectProgressUpdateError("checkin_not_found")

            previous_summary = str(project.get("status_summary") or "").strip()
            new_summary = str(output.get("new_status_summary") or "").strip()
            if not new_summary:
                new_summary = _merge_summary(previous_summary, output["progress_delta"])
            state_patch = output.get("project_state_patch") or {}
            if not str(state_patch.get("summary") or "").strip():
                state_patch["summary"] = new_summary
            facts = list(state_patch.get("facts") or [])
            facts.append(
                {
                    "type": "progress",
                    "text": output["progress_delta"],
                    "source_type": "daily_checkin",
                    "source_id": checkin_id,
                    "evidence_text": output["evidence_text"],
                }
            )
            state_patch["facts"] = facts

            event_id = repo.create_project_progress_event(
                connection,
                project_id=int(project["id"]),
                event_date=str(checkin["checkin_date"]),
                source_type="daily_checkin",
                source_id=checkin_id,
                event_status="active",
                progress_delta=output["progress_delta"],
                evidence_text=output["evidence_text"],
                confidence=output["confidence"],
                applied_to_summary=1,
                previous_status_summary=previous_summary,
                new_status_summary=new_summary,
                reason=output.get("reason"),
                llm_metadata=llm_metadata,
                raw_output=output,
            )
            updated_project = repo.update_project(
                connection,
                int(project["id"]),
                project_state=repo.merge_project_state(
                    project.get("project_state"),
                    state_patch,
                    updated_from={
                        "source": "project_progress",
                        "source_type": "daily_checkin",
                        "source_id": checkin_id,
                    },
                    replace_source_facts=True,
                    source_type="daily_checkin",
                    source_id=checkin_id,
                ),
            )
            if updated_project is None:
                raise ProjectProgressUpdateError("project_update_failed")
            return event_id, updated_project
    finally:
        connection.close()


def _restore_superseded_project_summaries(
    connection: sqlite3.Connection,
    old_events: list[dict[str, Any]],
) -> None:
    for event in old_events:
        project = repo.get_project(connection, int(event["project_id"]))
        if project is None:
            continue
        previous = event.get("previous_status_summary")
        new = event.get("new_status_summary")
        state_patch: dict[str, Any] = {}
        if previous is not None and str(project.get("status_summary") or "") == str(new or ""):
            state_patch["summary"] = str(previous or "")
        repo.update_project(
            connection,
            int(project["id"]),
            project_state=repo.merge_project_state(
                project.get("project_state"),
                state_patch,
                updated_from={
                    "source": "project_progress_restore",
                    "source_type": event.get("source_type"),
                    "source_id": event.get("source_id"),
                },
                replace_source_facts=True,
                source_type=str(event.get("source_type") or ""),
                source_id=event.get("source_id"),
            ),
        )


def _progress_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You update DayPilot project progress after a daily check-in.
Return one valid JSON object only. Do not include Markdown fences.
Trust the check-in content, but do not invent completed work not supported by evidence.
"""
    user = {
        "task": "Choose the related project and update its progress summary.",
        "required_json_fields": [
            "project_id",
            "confidence",
            "progress_delta",
            "new_status_summary",
            "project_state_patch",
            "evidence_text",
            "reason",
        ],
        "rules": [
            "Default to the daily_goal.project_id unless the check-in text clearly names another project.",
            "confidence is a number between 0 and 1 and is only for audit.",
            "new_status_summary may rewrite the old summary directly.",
            "project_state_patch is the canonical state update; include summary and progress facts when possible.",
            "Use concise Chinese for progress_delta and new_status_summary.",
        ],
        "projects": context["projects"],
        "daily_goal_project": context.get("daily_goal_project"),
        "daily_goal": context["daily_goal"],
        "active_version": context["active_version"],
        "checkin": context["checkin"],
        "recent_project_progress_events": context["recent_project_progress_events"],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _validate_progress_output(output: dict[str, Any], valid_project_ids: set[int]) -> None:
    normalized = _normalize_progress_output(output)
    if int(normalized["project_id"]) not in valid_project_ids:
        raise ValueError("invalid_project_id")


def _normalize_progress_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("project_progress_output_not_object")
    try:
        project_id = int(output.get("project_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("project_id_must_be_integer") from exc
    confidence = _clamp_float(output.get("confidence"), 0.0, 1.0)
    progress_delta = str(output.get("progress_delta") or "").strip()
    evidence_text = str(output.get("evidence_text") or "").strip()
    reason = str(output.get("reason") or "").strip()
    new_status_summary = str(output.get("new_status_summary") or "").strip()
    project_state_patch = output.get("project_state_patch")
    if not isinstance(project_state_patch, dict):
        project_state_patch = {}
    if new_status_summary and not str(project_state_patch.get("summary") or "").strip():
        project_state_patch["summary"] = new_status_summary
    facts = project_state_patch.get("facts")
    project_state_patch["facts"] = facts if isinstance(facts, list) else []
    if not progress_delta:
        raise ValueError("missing_progress_delta")
    if not evidence_text:
        raise ValueError("missing_evidence_text")
    return {
        "project_id": project_id,
        "confidence": confidence,
        "progress_delta": progress_delta,
        "new_status_summary": new_status_summary,
        "project_state_patch": project_state_patch,
        "evidence_text": evidence_text,
        "reason": reason,
    }


def _select_project_by_text(projects: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for project in projects:
        score = 0
        name = str(project.get("name") or "").lower()
        if name and name in lowered:
            score += 60
        for token in _project_tokens(project):
            if token and token.lower() in lowered:
                score += 12
        scored.append((score, project))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def _project_tokens(project: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("planning_bias") or ""),
            str(project.get("status_summary") or ""),
        ]
    )
    normalized = (
        text.replace("/", " ")
        .replace("+", " ")
        .replace("、", " ")
        .replace("，", " ")
        .replace(",", " ")
        .replace("。", " ")
    )
    tokens: list[str] = []
    for item in normalized.split():
        stripped = item.strip()
        if len(stripped) >= 2:
            tokens.append(stripped)
        if len(stripped) >= 4:
            tokens.extend(stripped[start : start + 4] for start in range(0, len(stripped) - 3))
    return list(dict.fromkeys(tokens))[:36]


def _checkin_text(context: dict[str, Any]) -> str:
    checkin = context.get("checkin") or {}
    return " ".join(
        [
            str(checkin.get("completion_text") or ""),
            str(checkin.get("tomorrow_direction") or ""),
        ]
    )


def _context_text(context: dict[str, Any]) -> str:
    active = context.get("active_version") or {}
    checkin = context.get("checkin") or {}
    return " ".join(
        [
            str(active.get("main_goal") or ""),
            str(active.get("goal_reason") or ""),
            str(checkin.get("completion_text") or ""),
            str(checkin.get("tomorrow_direction") or ""),
        ]
    )


def _merge_summary(current_summary: str, progress_delta: str) -> str:
    current = str(current_summary or "").strip()
    delta = str(progress_delta or "").strip()
    if not current:
        return delta[:240]
    if delta and delta in current:
        return current[:240]
    merged = f"{current}；最新进展：{delta}" if delta else current
    return merged[:240]


def _valid_priority(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"P0", "P1", "P2"} else "P2"


def _safe_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _compact_sentence(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:max_chars] or "Recorded a project progress update."


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
