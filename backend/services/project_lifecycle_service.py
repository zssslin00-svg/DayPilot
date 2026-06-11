from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import default_backup_dir
from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH


PROMPT_VERSION_MOCK = "project_lifecycle_v2_mock"
PROMPT_VERSION_DEEPSEEK = "project_lifecycle_v2_deepseek"
MOCK_MODEL_NAME = "mock-project-lifecycle-adapter"
PROJECT_BACKUP_DIR = default_backup_dir()

ACTION_VALUES = {"create_project", "complete_project", "update_project", "delete_project", "no_change"}
PRIORITY_VALUES = {"P0", "P1", "P2"}
TODAY_GOAL_POLICY_VALUES = {"keep", "refresh", "create", "remove"}


@dataclass(frozen=True)
class ProjectLifecycleResult:
    payload: dict[str, Any]


class ProjectLifecycleValidationError(ValueError):
    """Raised when a project lifecycle request is invalid."""


def get_project_overview(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        return {
            "active_projects": repo.list_projects(connection),
            "completed_projects": repo.list_completed_projects(connection, limit=8),
            "recent_lifecycle_events": repo.list_recent_project_lifecycle_events(connection, limit=8),
        }
    finally:
        connection.close()


def sync_current_projects_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    soul_path: str | Path = SOUL_PATH,
) -> Path:
    connection = initialize_database(db_path)
    try:
        active_projects = repo.list_projects(connection)
        return _sync_soul_current_projects(active_projects, Path(soul_path))
    finally:
        connection.close()


def apply_project_lifecycle_message(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
    today: date | None = None,
) -> ProjectLifecycleResult:
    message = str(request_body.get("message") or "").strip()
    if not message:
        raise ProjectLifecycleValidationError("message is required.")

    context = _build_lifecycle_context(db_path, message)
    try:
        llm_result = generate_json_with_fallback(
            task_name="project_lifecycle",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda soul: _lifecycle_messages(context, soul),
            mock_generate=lambda: MockProjectLifecycleAdapter().generate(context),
            validator=lambda output: _validate_lifecycle_batch_output(output, context),
            normalizer=_normalize_lifecycle_batch_output,
            settings=settings,
            soul_path=soul_path,
        )
        output = _normalize_lifecycle_batch_output(llm_result.output)
    except Exception as exc:  # noqa: BLE001 - bad model output must not mutate state
        return ProjectLifecycleResult(
            {
                "status": "failed",
                "reason": _safe_error(exc),
            }
        )

    try:
        payload = _apply_lifecycle_output(
            db_path,
            message=message,
            output=output,
            llm_metadata=llm_result.metadata,
            soul_path=Path(soul_path),
            today=today,
        )
    except Exception as exc:  # noqa: BLE001 - return a concise failure to the UI
        return ProjectLifecycleResult(
            {
                "status": "failed",
                "reason": _safe_error(exc),
            }
        )
    return ProjectLifecycleResult(payload)


def apply_project_lifecycle_output(
    db_path: str | Path,
    *,
    message: str,
    output: dict[str, Any],
    llm_metadata: dict[str, Any] | None = None,
    soul_path: str | Path = SOUL_PATH,
    today: date | None = None,
) -> ProjectLifecycleResult:
    normalized = _normalize_lifecycle_batch_output(output)
    payload = _apply_lifecycle_output(
        db_path,
        message=message,
        output=normalized,
        llm_metadata=llm_metadata or {},
        soul_path=Path(soul_path),
        today=today,
    )
    return ProjectLifecycleResult(payload)


class MockProjectLifecycleAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        message = context["message"]
        batch_outputs = _fallback_batch_outputs(message, context["all_projects"])
        if batch_outputs is not None:
            return {
                "schema_version": "project_lifecycle_batch.v1",
                "items": batch_outputs,
                "confidence": max(item.get("confidence", 0.0) for item in batch_outputs),
                "reason": "mock fallback parsed multiple project lifecycle items.",
            }
        single_output = _fallback_single_output(message, context["all_projects"])
        if single_output is not None:
            return {
                "schema_version": "project_lifecycle_batch.v1",
                "items": [single_output],
                "confidence": single_output.get("confidence", 0.0),
                "reason": single_output.get("reason") or "mock fallback parsed one project lifecycle item.",
            }
        raise ProjectLifecycleValidationError("fallback_could_not_parse_project_lifecycle_message")


def _fallback_single_output(message: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    create_output = _fallback_create_output(message)
    if create_output is not None:
        return create_output
    delete_output = _fallback_delete_output(message, projects)
    if delete_output is not None:
        return delete_output
    complete_output = _fallback_complete_output(message, projects)
    if complete_output is not None:
        return complete_output
    update_output = _fallback_update_output(message, projects)
    if update_output is not None:
        return update_output
    return None


def _fallback_batch_outputs(message: str, projects: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    parts = _split_lifecycle_message_parts(message)
    if len(parts) <= 1:
        return None
    outputs: list[dict[str, Any]] = []
    for part in parts:
        output = _fallback_single_output(part, projects)
        if output is not None:
            outputs.append(output)
    return outputs if outputs else None


def _split_lifecycle_message_parts(message: str) -> list[str]:
    line_parts = [_strip_lifecycle_bullet(line) for line in re.split(r"\r?\n", message)]
    line_parts = [part for part in line_parts if part]
    if len(line_parts) > 1:
        return line_parts
    split_parts = re.split(r"[;；。]\s*(?=(?:新增|添加|创建|删除|移除|完成|结束|更新|把))", message)
    return [part.strip() for part in split_parts if part.strip()]


def _strip_lifecycle_bullet(text: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", text).strip()


def _build_lifecycle_context(db_path: str | Path, message: str) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        return {
            "message": message,
            "active_projects": repo.list_projects(connection),
            "completed_projects": repo.list_completed_projects(connection, limit=8),
            "all_projects": repo.list_projects(connection, include_archived=True),
            "recent_lifecycle_events": repo.list_recent_project_lifecycle_events(connection, limit=8),
        }
    finally:
        connection.close()


def _lifecycle_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You update DayPilot project lifecycle state from a user's natural-language message.
Return one valid JSON object only. Do not include Markdown fences.
Do not invent completion or project details that the message does not support.
"""
    user = {
        "task": "Parse the user message into one or more project lifecycle actions.",
        "schema_version": "project_lifecycle_batch.v1",
        "required_json_shape": {
            "items": [
                {
                    "action": "create_project|update_project|complete_project|delete_project|no_change",
                    "project_name": "final project name, or empty for no_change",
                    "project_id": "existing project id when updating/completing/deleting, otherwise null",
                    "priority": "P0|P1|P2|null",
                    "status_summary": "current status/progress summary, empty if not stated",
                    "planning_bias": "planning guidance, empty if not stated",
                    "target_goal": "project final goal, empty if not stated",
                    "today_goal": "project today goal constraint, empty if not stated",
                    "project_state_patch": {
                        "summary": "current status/progress summary, empty if not stated",
                        "planning_guidance": "planning guidance, empty if not stated",
                        "target_goal": "project final goal, empty if not stated",
                        "today_goal": "project today goal constraint, empty if not stated",
                        "facts": [
                            {
                                "type": "progress|decision|constraint|next_step|artifact|risk|open_question|context",
                                "text": "one concise fact from the message",
                            }
                        ],
                    },
                    "completion_summary": "completion result, only for complete_project",
                    "today_goal_policy": "keep|refresh|create|remove",
                    "confidence": "0..1",
                    "reason": "concise parse reason",
                }
            ]
        },
        "actions": ["create_project", "complete_project", "update_project", "delete_project", "no_change"],
        "today_goal_policies": ["keep", "refresh", "create", "remove"],
        "rules": [
            "Use create_project for a new project.",
            "Use complete_project when the user says an existing project is finished.",
            "Use update_project when the user updates a project's current progress, goal, priority, or name.",
            "For update_project rename messages, project_id must be the existing project being renamed; project_name must be the final new name.",
            "Use delete_project only when the user explicitly asks to delete, remove, or stop tracking an existing project.",
            "Use no_change only when the message is not about project lifecycle.",
            "Return one item per affected project. If multiple projects are mentioned, include multiple items.",
            "For create_project, default priority to P2 unless the user says P0, P1, or P2.",
            "For complete_project, update_project, or delete_project, choose an existing project_id when possible.",
            "Set today_goal_policy to create for new projects, remove for completed/deleted projects, refresh for project name/progress/target/priority changes, and keep only for no current-state change.",
            "Map 项目最终目标 to target_goal and 项目今日目标 to today_goal.",
            "For legacy 目标 wording without 项目最终目标 or 项目今日目标, treat it as today_goal; if target_goal is otherwise empty, copy it to target_goal for compatibility.",
            "Use concise Chinese for status_summary and planning_bias.",
            "Prefer project_state_patch as the canonical current project state update; status_summary/planning_bias/target_goal/today_goal are backward-compatible mirrors.",
        ],
        "message": context["message"],
        "active_projects": context["active_projects"],
        "completed_projects": context["completed_projects"],
        "recent_lifecycle_events": context["recent_lifecycle_events"],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _validate_lifecycle_batch_output(output: dict[str, Any], context: dict[str, Any]) -> None:
    normalized = _normalize_lifecycle_batch_output(output)
    if not normalized["items"]:
        raise ValueError("missing_project_lifecycle_items")
    for item in normalized["items"]:
        if item["action"] == "create_project" and not item["project_name"]:
            raise ValueError("missing_project_name")


def _normalize_lifecycle_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("project_lifecycle_output_not_object")
    action = str(output.get("action") or "").strip()
    if action not in ACTION_VALUES:
        raise ValueError("invalid_project_lifecycle_action")
    priority = _normalize_priority(output.get("priority"))
    priority_explicit = priority is not None
    if action == "create_project" and priority is None:
        priority = "P2"
    confidence = _clamp_float(output.get("confidence"), 0.0, 1.0)
    project_id = _safe_positive_int(output.get("project_id"))
    today_goal_policy = _normalize_today_goal_policy(output.get("today_goal_policy"), action)
    status_summary = str(output.get("status_summary") or output.get("progress") or "").strip()
    planning_bias = str(output.get("planning_bias") or "").strip()
    target_goal = str(output.get("target_goal") or "").strip()
    today_goal = str(output.get("today_goal") or "").strip()
    return {
        "action": action,
        "project_id": project_id,
        "project_name": str(output.get("project_name") or "").strip(),
        "priority": priority,
        "priority_explicit": priority_explicit,
        "status_summary": status_summary,
        "planning_bias": planning_bias,
        "target_goal": target_goal,
        "today_goal": today_goal,
        "project_state_patch": _normalize_project_state_patch(
            output.get("project_state_patch"),
            status_summary=status_summary,
            planning_bias=planning_bias,
            target_goal=target_goal,
            today_goal=today_goal,
            completion_summary=str(output.get("completion_summary") or "").strip(),
            action=action,
        ),
        "completion_summary": str(output.get("completion_summary") or "").strip(),
        "today_goal_policy": today_goal_policy,
        "confidence": confidence,
        "reason": str(output.get("reason") or "").strip(),
    }


def _normalize_lifecycle_batch_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("project_lifecycle_output_not_object")
    raw_items = output.get("items")
    if raw_items is None:
        raw_items = [output]
    if not isinstance(raw_items, list):
        raise ValueError("project_lifecycle_items_not_array")
    items = [_normalize_lifecycle_output(item) for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        raise ValueError("project_lifecycle_item_not_object")
    return {
        "schema_version": "project_lifecycle_batch.v1",
        "items": items,
        "confidence": _clamp_float(output.get("confidence"), 0.0, 1.0),
        "reason": str(output.get("reason") or "").strip(),
    }


def _apply_lifecycle_output(
    db_path: str | Path,
    *,
    message: str,
    output: dict[str, Any],
    llm_metadata: dict[str, Any],
    soul_path: Path,
    today: date | None,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        items: list[dict[str, Any]] = []
        active_projects: list[dict[str, Any]] = []
        seen_project_keys: set[str] = set()
        with connection:
            for index, item in enumerate(output["items"], start=1):
                items.append(
                    _apply_lifecycle_item_with_savepoint(
                        connection,
                        message=message,
                        item=item,
                        llm_metadata=llm_metadata,
                        item_index=index,
                        seen_project_keys=seen_project_keys,
                    )
                )
            active_projects = repo.list_projects(connection)

        applied_items = [item for item in items if item["status"] == "applied"]
        soul_backup: Path | None = None
        soul_sync_error: str | None = None
        soul_sync_retry_job_id: int | None = None
        if applied_items:
            try:
                soul_backup = _sync_soul_current_projects(active_projects, soul_path)
            except Exception as exc:  # noqa: BLE001 - project DB state is the source of truth
                soul_sync_error = _safe_error(exc)
                from backend.services.soul_sync_service import enqueue_soul_sync_retry

                source_id = applied_items[0].get("event_id")
                soul_sync_retry_job_id = enqueue_soul_sync_retry(
                    db_path,
                    job_type="project_lifecycle",
                    source_table="project_lifecycle_events",
                    source_id=source_id,
                    payload={
                        "project_lifecycle_event_id": source_id,
                        "action": "batch_project_lifecycle",
                    },
                    error=soul_sync_error,
                )

        _apply_today_goal_refreshes(db_path, today, items, soul_path=soul_path)

        return _batch_payload(
            items,
            llm_metadata=llm_metadata,
            soul_backup=soul_backup,
            soul_sync_error=soul_sync_error,
            soul_sync_retry_job_id=soul_sync_retry_job_id,
        )
    finally:
        connection.close()


def _apply_lifecycle_item_with_savepoint(
    connection: sqlite3.Connection,
    *,
    message: str,
    item: dict[str, Any],
    llm_metadata: dict[str, Any],
    item_index: int,
    seen_project_keys: set[str],
) -> dict[str, Any]:
    savepoint = f"project_lifecycle_item_{item_index}"
    item_metadata = {**llm_metadata, "batch_item_index": item_index}
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        duplicate_key = _lifecycle_item_duplicate_key(connection, item)
        if duplicate_key is not None and duplicate_key in seen_project_keys:
            raise ProjectLifecycleValidationError("duplicate_project_in_batch")

        result = _apply_lifecycle_item(connection, item)
        if result.get("applied"):
            _sync_user_profile_projects(connection)
        event_id = _create_lifecycle_event(
            connection,
            message=message,
            item=item,
            result=result,
            llm_metadata=item_metadata,
        )
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        if result.get("applied"):
            for key in _lifecycle_result_duplicate_keys(result, item, duplicate_key):
                seen_project_keys.add(key)
        return _item_payload_from_result(item, result, event_id)
    except Exception as exc:  # noqa: BLE001 - this item fails without blocking other items
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        reason = _safe_error(exc)
        event_id = _create_failed_lifecycle_event(
            connection,
            message=message,
            item=item,
            llm_metadata=item_metadata,
            reason=reason,
        )
        return _failed_item_payload(item, reason, event_id)


def _apply_lifecycle_item(connection: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    if item["action"] == "create_project":
        return _apply_create_project(connection, item)
    if item["action"] == "complete_project":
        return _apply_complete_project(connection, item)
    if item["action"] == "update_project":
        return _apply_update_project(connection, item)
    if item["action"] == "delete_project":
        return _apply_delete_project(connection, item)
    return _apply_no_change(connection, item)


def _create_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    message: str,
    item: dict[str, Any],
    result: dict[str, Any],
    llm_metadata: dict[str, Any],
) -> int:
    return repo.create_project_lifecycle_event(
        connection,
        raw_message=message,
        action=result["event_action"],
        project_id=result.get("event_project_id", result.get("project_id")),
        project_name=result.get("project_name") or item.get("project_name"),
        priority=result.get("priority") or item.get("priority"),
        previous_status=result.get("previous_status"),
        new_status=result.get("new_status"),
        previous_status_summary=result.get("previous_status_summary"),
        new_status_summary=result.get("new_status_summary"),
        planning_bias=result.get("planning_bias") or item.get("planning_bias"),
        confidence=item.get("confidence"),
        applied=1 if result.get("applied") else 0,
        reason=item.get("reason"),
        llm_metadata=llm_metadata,
        raw_output=item,
    )


def _create_failed_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    message: str,
    item: dict[str, Any],
    llm_metadata: dict[str, Any],
    reason: str,
) -> int:
    project_id = item.get("project_id")
    if project_id is not None and repo.get_project(connection, int(project_id)) is None:
        project_id = None
    return repo.create_project_lifecycle_event(
        connection,
        raw_message=message,
        action=item["action"],
        project_id=project_id,
        project_name=item.get("project_name"),
        priority=item.get("priority"),
        previous_status=None,
        new_status=None,
        previous_status_summary=None,
        new_status_summary=item.get("status_summary"),
        planning_bias=item.get("planning_bias"),
        confidence=item.get("confidence"),
        applied=0,
        reason=reason,
        llm_metadata=llm_metadata,
        raw_output=item,
    )


def _item_payload_from_result(
    item: dict[str, Any],
    result: dict[str, Any],
    event_id: int,
) -> dict[str, Any]:
    project = _project_payload(result.get("project"))
    policy = _final_today_goal_policy(item, result)
    return {
        "status": "applied" if result.get("applied") else "no_change",
        "action": result["event_action"],
        "project_id": result.get("project_id"),
        "project_name": result.get("project_name") or item.get("project_name"),
        "project": project,
        "status_summary": result.get("new_status_summary") or item.get("status_summary") or "",
        "today_goal_policy": policy,
        "today_goal_refresh": _initial_today_goal_refresh_state(policy),
        "reason": item.get("reason"),
        "event_id": event_id,
        "project_lifecycle_event_id": event_id,
        "message": result.get("message") or "Project lifecycle item applied.",
    }


def _failed_item_payload(item: dict[str, Any], reason: str, event_id: int) -> dict[str, Any]:
    return {
        "status": "failed",
        "action": item["action"],
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "project": None,
        "status_summary": item.get("status_summary") or "",
        "today_goal_policy": "keep",
        "today_goal_refresh": "skipped_failed_item",
        "reason": reason,
        "event_id": event_id,
        "project_lifecycle_event_id": event_id,
        "message": "Project lifecycle item failed.",
    }


def _batch_payload(
    items: list[dict[str, Any]],
    *,
    llm_metadata: dict[str, Any],
    soul_backup: Path | None,
    soul_sync_error: str | None,
    soul_sync_retry_job_id: int | None,
) -> dict[str, Any]:
    applied_count = sum(1 for item in items if item["status"] == "applied")
    failed_count = sum(1 for item in items if item["status"] == "failed")
    refresh_failed_count = sum(1 for item in items if item.get("today_goal_refresh") == "failed")
    if failed_count and applied_count:
        status = "partial"
    elif failed_count:
        status = "failed"
    elif applied_count:
        status = "applied"
    else:
        status = "no_change"

    first = items[0] if items else {}
    single_item = len(items) == 1
    return {
        "status": status,
        "action": first.get("action") if single_item else "batch_project_lifecycle",
        "project": first.get("project") if single_item else None,
        "project_lifecycle_event_id": first.get("event_id"),
        "items": items,
        "applied_count": applied_count,
        "failed_count": failed_count,
        "today_goal_refresh_failed_count": refresh_failed_count,
        "soul_synced": bool(soul_backup) if applied_count else False,
        "soul_backup": str(soul_backup) if soul_backup else None,
        "fallback_reason": llm_metadata.get("fallback_reason"),
        "soul_sync_queued": soul_sync_retry_job_id is not None,
        "soul_sync_retry_job_id": soul_sync_retry_job_id,
        "soul_sync_error": soul_sync_error,
        "reason": first.get("reason") if status == "failed" else None,
        "message": _batch_message(status, applied_count, failed_count, refresh_failed_count),
    }


def _batch_message(status: str, applied_count: int, failed_count: int, refresh_failed_count: int) -> str:
    if status == "failed":
        return "项目更新失败。"
    if status == "partial":
        return f"项目更新部分完成：成功 {applied_count} 项，失败 {failed_count} 项。"
    if status == "no_change":
        return "没有识别到需要更新的项目信息。"
    if refresh_failed_count:
        return f"项目更新已完成，{refresh_failed_count} 项今日目标刷新失败。"
    return "项目信息已更新。"


def _lifecycle_item_duplicate_key(connection: sqlite3.Connection, item: dict[str, Any]) -> str | None:
    if item["action"] == "no_change":
        return None
    project_id = item.get("project_id")
    if project_id is not None:
        return f"project:{int(project_id)}"
    if item["action"] == "create_project":
        existing = repo.get_project_by_name(connection, item["project_name"])
        if existing is not None:
            return f"project:{int(existing['id'])}"
        return f"create:{_normalized_name_key(item['project_name'])}"
    project = _resolve_project(repo.list_projects(connection, include_archived=True), item)
    return f"project:{int(project['id'])}"


def _lifecycle_result_duplicate_keys(
    result: dict[str, Any],
    item: dict[str, Any],
    duplicate_key: str | None,
) -> set[str]:
    keys: set[str] = set()
    if duplicate_key is not None:
        keys.add(duplicate_key)
    project_id = result.get("project_id") or item.get("project_id")
    if project_id is not None:
        keys.add(f"project:{int(project_id)}")
    project_name = result.get("project_name") or item.get("project_name")
    if project_name:
        keys.add(f"create:{_normalized_name_key(project_name)}")
    return keys


def _normalized_name_key(name: Any) -> str:
    return str(name or "").strip().casefold()


def _final_today_goal_policy(item: dict[str, Any], result: dict[str, Any]) -> str:
    if not result.get("applied"):
        return "keep"
    action = result["event_action"]
    if action == "create_project":
        return "create"
    if action in {"complete_project", "delete_project"}:
        return "remove"
    if action == "update_project":
        return "refresh" if _project_state_changed_for_goal(result) else "keep"
    return _normalize_today_goal_policy(item.get("today_goal_policy"), action)


def _project_state_changed_for_goal(result: dict[str, Any]) -> bool:
    pairs = [
        ("previous_project_name", "new_project_name"),
        ("previous_status_summary", "new_status_summary"),
        ("previous_planning_bias", "new_planning_bias"),
        ("previous_priority", "new_priority"),
        ("previous_target_goal", "new_target_goal"),
        ("previous_today_goal", "new_today_goal"),
        ("previous_project_state_hash", "new_project_state_hash"),
    ]
    for previous_key, new_key in pairs:
        if _diff_value(result.get(previous_key)) != _diff_value(result.get(new_key)):
            return True
    return False


def _diff_value(value: Any) -> str:
    return str(value or "").strip()


def _initial_today_goal_refresh_state(policy: str) -> str:
    if policy == "keep":
        return "kept"
    if policy == "remove":
        return "removed"
    return "pending"


def _apply_today_goal_refreshes(
    db_path: str | Path,
    today: date | None,
    items: list[dict[str, Any]],
    *,
    soul_path: Path,
) -> None:
    if today is None:
        for item in items:
            if item.get("today_goal_refresh") == "pending":
                item["today_goal_refresh"] = "skipped_no_date"
        return

    from backend.services.today_goal_service import refresh_today_goal_for_project

    for item in items:
        policy = item.get("today_goal_policy")
        if item.get("status") != "applied" or policy not in {"create", "refresh"}:
            continue
        project_id = item.get("project_id")
        if project_id is None:
            item["today_goal_refresh"] = "skipped_no_project"
            continue
        try:
            refresh_result = refresh_today_goal_for_project(
                db_path,
                today,
                int(project_id),
                force=policy == "refresh",
                revision_reason="Project lifecycle update refreshed this project goal.",
                soul_path=soul_path,
            )
        except Exception as exc:  # noqa: BLE001 - goal refresh must not roll back project lifecycle writes
            item["today_goal_refresh"] = "failed"
            item["today_goal_refresh_reason"] = _safe_error(exc)
            continue
        item["today_goal_refresh"] = refresh_result.status
        item["today_goal_refresh_reason"] = None


def _apply_create_project(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    project_name = output["project_name"]
    existing = repo.get_project_by_name(connection, project_name)
    status_summary = output["status_summary"]
    planning_bias = output["planning_bias"] or _default_planning_bias(output)
    if existing is not None:
        previous_summary = str(existing.get("status_summary") or "")
        previous_planning_bias = str(existing.get("planning_bias") or "")
        previous_priority = existing.get("priority") or "P2"
        previous_target_goal = _target_goal_from_project(existing)
        previous_today_goal = _today_goal_from_project(existing)
        priority = output["priority"] if output.get("priority_explicit") else previous_priority
        next_summary = status_summary or previous_summary
        next_planning_bias = output["planning_bias"] or previous_planning_bias
        next_target_goal = output.get("target_goal") or previous_target_goal
        next_today_goal = output.get("today_goal") or previous_today_goal
        source_output = {
            **output,
            "project_name": project_name,
            "priority": priority,
            "target_goal": next_target_goal,
            "today_goal": next_today_goal,
        }
        updated = repo.update_project(
            connection,
            int(existing["id"]),
            priority=priority,
            role=existing.get("role") or _role_for_priority(priority),
            status="active",
            status_summary=next_summary,
            planning_bias=next_planning_bias,
            source_payload=_source_payload(source_output, next_summary, next_planning_bias, existing=existing),
        )
        return {
            "applied": True,
            "event_action": "update_project",
            "project": updated,
            "project_id": updated["id"] if updated else existing["id"],
            "project_name": project_name,
            "priority": priority,
            "previous_project_name": existing.get("name"),
            "new_project_name": project_name,
            "previous_status": existing.get("status"),
            "new_status": "active",
            "previous_status_summary": previous_summary,
            "new_status_summary": (updated or existing).get("status_summary"),
            "previous_planning_bias": previous_planning_bias,
            "new_planning_bias": next_planning_bias,
            "previous_priority": previous_priority,
            "new_priority": priority,
            "previous_target_goal": previous_target_goal,
            "new_target_goal": next_target_goal,
            "previous_today_goal": previous_today_goal,
            "new_today_goal": next_today_goal,
            "previous_project_state_hash": repo.project_state_hash(existing),
            "new_project_state_hash": repo.project_state_hash(updated or existing),
            "planning_bias": next_planning_bias,
            "message": "项目已存在，已更新为当前项目。",
        }

    source_payload = _source_payload(output, status_summary, planning_bias)
    project_id = repo.create_project(
        connection,
        name=project_name,
        priority=output["priority"],
        role=_role_for_priority(output["priority"]),
        status="active",
        status_summary=status_summary,
        planning_bias=planning_bias,
        source_payload=source_payload,
    )
    project = repo.get_project(connection, project_id)
    return {
        "applied": True,
        "event_action": "create_project",
        "project": project,
        "project_id": project_id,
        "project_name": project_name,
        "priority": output["priority"],
        "previous_project_name": None,
        "new_project_name": project_name,
        "previous_status": None,
        "new_status": "active",
        "previous_status_summary": None,
        "new_status_summary": status_summary,
        "previous_planning_bias": None,
        "new_planning_bias": planning_bias,
        "previous_priority": None,
        "new_priority": output["priority"],
        "previous_target_goal": None,
        "new_target_goal": output.get("target_goal") or "",
        "previous_today_goal": None,
        "new_today_goal": output.get("today_goal") or "",
        "previous_project_state_hash": None,
        "new_project_state_hash": repo.project_state_hash(project),
        "planning_bias": planning_bias,
        "message": "项目已新增。",
    }


def _apply_complete_project(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    project = _resolve_project(repo.list_projects(connection, include_archived=True), output)
    previous_summary = str(project.get("status_summary") or "")
    new_summary = output["completion_summary"] or output["status_summary"] or previous_summary
    updated = repo.update_project(
        connection,
        int(project["id"]),
        status="completed",
        status_summary=new_summary,
    )
    return {
        "applied": True,
        "event_action": "complete_project",
        "project": updated,
        "project_id": project["id"],
        "project_name": project["name"],
        "priority": project.get("priority"),
        "previous_status": project.get("status"),
        "new_status": "completed",
        "previous_status_summary": previous_summary,
        "new_status_summary": new_summary,
        "planning_bias": project.get("planning_bias"),
        "message": "项目已标记完成，并从当前项目中隐藏。",
    }


def _apply_update_project(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    project = _resolve_project(repo.list_projects(connection, include_archived=True), output)
    next_name = output["project_name"] or str(project.get("name") or "")
    if not next_name:
        raise ProjectLifecycleValidationError("missing_project_name")
    existing_with_name = repo.get_project_by_name(connection, next_name)
    if existing_with_name is not None and int(existing_with_name["id"]) != int(project["id"]):
        raise ProjectLifecycleValidationError("project_name_conflict")

    previous_summary = str(project.get("status_summary") or "")
    previous_planning_bias = str(project.get("planning_bias") or "")
    previous_priority = project.get("priority") or "P2"
    previous_target_goal = _target_goal_from_project(project)
    previous_today_goal = _today_goal_from_project(project)
    status_summary = output["status_summary"] or previous_summary
    planning_bias = output["planning_bias"] or previous_planning_bias
    priority = output["priority"] or previous_priority
    target_goal = output.get("target_goal") or previous_target_goal
    today_goal = output.get("today_goal") or previous_today_goal
    source_output = {
        **output,
        "project_name": next_name,
        "priority": priority,
        "target_goal": target_goal,
        "today_goal": today_goal,
    }
    updated = repo.update_project(
        connection,
        int(project["id"]),
        name=next_name,
        priority=priority,
        role=project.get("role") or _role_for_priority(priority),
        status="active" if project.get("status") == "completed" else project.get("status"),
        status_summary=status_summary,
        planning_bias=planning_bias,
        source_payload=_source_payload(source_output, status_summary, planning_bias, existing=project),
    )
    return {
        "applied": True,
        "event_action": "update_project",
        "project": updated,
        "project_id": project["id"],
        "project_name": next_name,
        "priority": (updated or project).get("priority"),
        "previous_project_name": project.get("name"),
        "new_project_name": next_name,
        "previous_status": project.get("status"),
        "new_status": (updated or project).get("status"),
        "previous_status_summary": previous_summary,
        "new_status_summary": status_summary,
        "previous_planning_bias": previous_planning_bias,
        "new_planning_bias": planning_bias,
        "previous_priority": previous_priority,
        "new_priority": priority,
        "previous_target_goal": previous_target_goal,
        "new_target_goal": target_goal,
        "previous_today_goal": previous_today_goal,
        "new_today_goal": today_goal,
        "previous_project_state_hash": repo.project_state_hash(project),
        "new_project_state_hash": repo.project_state_hash(updated or project),
        "planning_bias": planning_bias,
        "message": "项目信息已更新。",
    }


def _apply_delete_project(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    project = _resolve_project(repo.list_projects(connection, include_archived=True), output)
    previous_summary = str(project.get("status_summary") or "")
    deleted = repo.delete_project(connection, int(project["id"]))
    if deleted is None:
        raise ProjectLifecycleValidationError("project_not_found")
    return {
        "applied": True,
        "event_action": "delete_project",
        "project": deleted,
        "project_id": None,
        "event_project_id": None,
        "deleted_project_id": project["id"],
        "project_name": project["name"],
        "priority": project.get("priority"),
        "previous_status": project.get("status"),
        "new_status": "deleted",
        "previous_status_summary": previous_summary,
        "new_status_summary": "",
        "planning_bias": project.get("planning_bias"),
        "message": "项目已删除。",
    }


def _apply_no_change(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "applied": False,
        "event_action": "no_change",
        "project_id": None,
        "project_name": output.get("project_name"),
        "priority": output.get("priority"),
        "message": "没有识别到需要更新的项目信息。",
    }


def _sync_user_profile_projects(connection: sqlite3.Connection) -> None:
    active_projects = repo.list_projects(connection)
    project_priorities = [
        {
            "priority": project["priority"],
            "role": project.get("role") or _role_for_priority(project.get("priority")),
            "name": project["name"],
            "progress": project.get("status_summary") or "",
            "planning_bias": project.get("planning_bias") or "",
            "target_goal": repo.project_target_goal(project),
            "today_goal": repo.project_today_goal(project),
            "id": project["id"],
        }
        for project in active_projects
    ]
    profile = repo.get_user_profile(connection)
    if profile is None:
        repo.create_user_profile(
            connection,
            id=1,
            long_term_direction="项目状态由 SOUL.md 和 DayPilot 项目更新共同管理。",
            current_focus_projects=[project["name"] for project in active_projects],
            goal_preferences={
                "project_priorities": project_priorities,
                "updated_from_project_lifecycle_at": _now_text(),
            },
        )
        return
    preferences = dict(profile.get("goal_preferences") or {})
    preferences["project_priorities"] = project_priorities
    policy = preferences.get("priority_policy")
    if isinstance(policy, dict):
        policy["order"] = _priority_order_for_projects(active_projects)
    preferences["updated_from_project_lifecycle_at"] = _now_text()
    repo.update_user_profile(
        connection,
        int(profile["id"]),
        goal_preferences=preferences,
        current_focus_projects=[project["name"] for project in active_projects],
    )


def _sync_soul_current_projects(active_projects: list[dict[str, Any]], soul_path: Path) -> Path:
    text = soul_path.read_text(encoding="utf-8")
    start_marker = "## 当前项目"
    end_marker = "## 用户偏好"
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end <= start:
        raise ProjectLifecycleValidationError("SOUL.md current project section markers were not found.")

    backup_path = _backup_soul(soul_path)
    rendered = _render_current_projects_section(active_projects)
    next_text = text[:start] + rendered + "\n\n" + text[end:]
    soul_path.write_text(next_text, encoding="utf-8")
    return backup_path


def _render_current_projects_section(active_projects: list[dict[str, Any]]) -> str:
    projects = sorted(active_projects, key=lambda item: int(item["id"]))
    lines = [
        "## 当前项目",
        "",
        f"当前 active 项目有 {len(projects)} 个。每日目标生成时，每个 active 项目都要生成一个符合用户习惯的今日目标；不要在多个项目之间挑选单一主目标。",
        "",
    ]
    if not projects:
        lines.append("暂无 active 项目。")
        return "\n".join(lines).rstrip()

    for index, project in enumerate(projects, start=1):
        lines.append(_render_current_project_line(index, project))
    lines.extend(
        [
            "",
            "本段落由 DayPilot 管理，也可以手动编辑。使用单行清单维护 active 项目；从列表移除的项目会标记完成，写“暂无 active 项目。”表示当前没有 active 项目。",
            "",
            "每日生成规则：",
            "",
            "- 每个 active 项目都生成一个今日目标。",
            "- 昨日显式完成的项目生成新的推进目标。",
            "- 昨日未完成或未 check-in 的项目继续承接未完成目标。",
            "- 目标必须保留可检查产出，范围符合用户可用时间和偏好。",
        ]
    )
    return "\n".join(lines).rstrip()


def _render_current_project_line(index: int, project: dict[str, Any]) -> str:
    priority = str(project.get("priority") or "P2").strip() or "P2"
    name = _soul_line_value(project.get("name"), limit=120)
    summary = _soul_line_value(project.get("status_summary"), limit=180)
    target = _soul_line_value(repo.project_target_goal(project), limit=160)
    today_goal = _soul_line_value(repo.project_today_goal(project), limit=160)
    parts = [f"{priority} {name}"]
    if summary:
        parts.append(f"当前进度：{summary}")
    if target:
        parts.append(f"项目最终目标：{target}")
    if today_goal:
        parts.append(f"项目今日目标：{today_goal}")
    return f"{index}. {'；'.join(parts)}。"


def _soul_line_value(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[。；;]+", "，", text).strip(" ，,")
    return text[:limit].strip()


def _backup_soul(soul_path: Path) -> Path:
    PROJECT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = PROJECT_BACKUP_DIR / f"SOUL_{stamp}.md"
    shutil.copy2(soul_path, backup_path)
    return backup_path


def _resolve_project(projects: list[dict[str, Any]], output: dict[str, Any]) -> dict[str, Any]:
    project_id = output.get("project_id")
    if project_id is not None:
        for project in projects:
            if int(project["id"]) == int(project_id):
                return project
    project_name = str(output.get("project_name") or "").strip()
    for project in projects:
        if project_name and project_name == str(project.get("name") or ""):
            return project
    for project in projects:
        name = str(project.get("name") or "")
        if project_name and (project_name in name or name in project_name):
            return project
    raise ValueError("project_not_found")


def _fallback_create_output(message: str) -> dict[str, Any] | None:
    match = re.search(r"新增\s*(P[012])?\s*项目\s*[:：]\s*(.+)", message, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    priority = _valid_priority(match.group(1))
    remainder = match.group(2).strip()
    project_name = _first_segment(remainder, _project_detail_stops())
    if not project_name:
        return None
    progress = _field_segment(message, ["当前进度", "进度"]) or ""
    target, today_goal = _extract_project_goals(message)
    return {
        "action": "create_project",
        "project_id": None,
        "project_name": project_name,
        "priority": priority,
        "status_summary": progress,
        "planning_bias": _planning_bias_from_target(target or today_goal),
        "target_goal": target,
        "today_goal": today_goal,
        "completion_summary": "",
        "confidence": 0.55,
        "reason": "mock fallback parsed an explicit create project message.",
    }


def _fallback_delete_output(message: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    if any(token in message for token in ("不要删除", "别删除", "无需删除", "不用删除", "不要移除")):
        return None
    if not any(token in message for token in ("删除", "删掉", "移除", "删减", "不再跟踪", "不要再跟踪")):
        return None
    project = _project_mentioned_once(projects, message)
    if project is None:
        match = re.search(r"(?:删除|删掉|移除|删减|不再跟踪|不要再跟踪)(?:项目)?\s*[:：]?\s*(.+)", message, flags=re.DOTALL)
        if match:
            project = _project_mentioned_once(projects, _first_segment(match.group(1), ["。", "；", "\n"]))
    if project is None:
        return None
    return {
        "action": "delete_project",
        "project_id": project["id"],
        "project_name": project["name"],
        "priority": project.get("priority") or "P2",
        "status_summary": str(project.get("status_summary") or ""),
        "planning_bias": str(project.get("planning_bias") or ""),
        "target_goal": "",
        "today_goal": "",
        "completion_summary": "",
        "confidence": 0.6,
        "reason": "mock fallback matched an explicit delete project message.",
    }


def _fallback_complete_output(message: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    if "完成" not in message and "结束" not in message:
        return None
    for project in projects:
        name = str(project.get("name") or "")
        if name and name in message:
            return {
                "action": "complete_project",
                "project_id": project["id"],
                "project_name": name,
                "priority": project.get("priority") or "P2",
                "status_summary": str(project.get("status_summary") or ""),
                "planning_bias": str(project.get("planning_bias") or ""),
                "target_goal": "",
                "today_goal": "",
                "completion_summary": _completion_summary(message, name),
                "confidence": 0.55,
                "reason": "mock fallback matched an existing project name and completion wording.",
            }
    match = re.search(r"完成项目\s*[:：]\s*(.+)", message, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    name = _first_segment(match.group(1), ["。", "\n"])
    for project in projects:
        if name == str(project.get("name") or ""):
            return {
                "action": "complete_project",
                "project_id": project["id"],
                "project_name": name,
                "priority": project.get("priority") or "P2",
                "status_summary": str(project.get("status_summary") or ""),
                "planning_bias": str(project.get("planning_bias") or ""),
                "target_goal": "",
                "today_goal": "",
                "completion_summary": _completion_summary(message, name),
                "confidence": 0.55,
                "reason": "mock fallback parsed an explicit complete project message.",
            }
    return None


def _fallback_update_output(message: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    rename_match = re.search(
        r"把\s*(?:这个项目)?\s*(?P<old>.+?)\s*(?:改成|改为|重命名为|改名为)\s*(?:下面这个项目\s*[:：])?\s*(?P<new>.+)",
        message,
        flags=re.DOTALL,
    )
    if rename_match:
        old_text = _first_segment(rename_match.group("old"), ["。", "；", "\n"])
        project = _project_mentioned_once(projects, old_text) or _project_mentioned_once(projects, message)
        new_name = _first_segment(rename_match.group("new"), _project_detail_stops() + ["；"])
        if project is not None and new_name:
            progress = _field_segment(message, ["当前进度", "进度"]) or str(project.get("status_summary") or "")
            target, today_goal = _extract_project_goals(message)
            planning_bias = (
                _planning_bias_from_target(target or today_goal)
                if today_goal or target
                else str(project.get("planning_bias") or _default_planning_bias_for_project(project))
            )
            return {
                "action": "update_project",
                "project_id": project["id"],
                "project_name": new_name,
                "priority": _priority_from_message(message) or project.get("priority") or "P2",
                "status_summary": progress,
                "planning_bias": planning_bias,
                "target_goal": target,
                "today_goal": today_goal,
                "completion_summary": "",
                "confidence": 0.65,
                "reason": "mock fallback parsed a project rename/update message.",
            }

    if not any(token in message for token in ("更新", "当前进度", "进度", "优先级", "目标")):
        return None
    project = _project_mentioned_once(projects, message)
    if project is None:
        return None
    progress = _field_segment(message, ["当前进度", "进度"])
    target, today_goal = _extract_project_goals(message)
    priority = _priority_from_message(message)
    if not progress and not target and not today_goal and priority is None:
        return None
    planning_bias = (
        _planning_bias_from_target(target or today_goal)
        if today_goal or target
        else str(project.get("planning_bias") or _default_planning_bias_for_project(project))
    )
    return {
        "action": "update_project",
        "project_id": project["id"],
        "project_name": project["name"],
        "priority": priority or project.get("priority") or "P2",
        "status_summary": progress or str(project.get("status_summary") or ""),
        "planning_bias": planning_bias,
        "target_goal": target or "",
        "today_goal": today_goal or "",
        "completion_summary": "",
        "confidence": 0.6,
        "reason": "mock fallback parsed a project progress update message.",
    }


def _extract_project_goals(message: str) -> tuple[str, str]:
    target_goal = _field_segment(message, ["项目最终目标", "最终目标"])
    today_goal = _field_segment(message, ["项目今日目标", "今日目标"])
    if not target_goal and not today_goal:
        legacy_goal = _field_segment(message, ["目标", "本周目标", "希望推进到"]) or ""
        return legacy_goal, legacy_goal
    return target_goal or "", today_goal or ""


def _project_detail_stops() -> list[str]:
    return ["当前进度", "进度", "项目最终目标", "最终目标", "项目今日目标", "今日目标", "目标", "。", "\n"]


def _field_segment(message: str, labels: list[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*(?:[:：]|是)\s*(.+)", message, flags=re.DOTALL)
        if match:
            return _first_segment(match.group(1), _field_value_stops())
    return None


def _field_value_stops() -> list[str]:
    return ["。", "；", "\n", "项目最终目标", "最终目标", "项目今日目标", "今日目标"]


def _first_segment(text: str, stops: list[str]) -> str:
    end = len(text)
    for stop in stops:
        index = text.find(stop)
        if index >= 0:
            end = min(end, index)
    return text[:end].strip(" ：:，,；;。")


def _completion_summary(message: str, project_name: str) -> str:
    text = message.replace(project_name, "").strip(" ：:，,；;。")
    return text[:240] or "项目已完成。"


def _project_mentioned_once(projects: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    matches = [
        project
        for project in projects
        if str(project.get("name") or "") and str(project.get("name") or "") in text
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: len(str(item.get("name") or "")), reverse=True)
    if len(matches) == 1:
        return matches[0]
    first_len = len(str(matches[0].get("name") or ""))
    second_len = len(str(matches[1].get("name") or ""))
    return matches[0] if first_len > second_len else None


def _priority_from_message(message: str) -> str | None:
    match = re.search(r"P[012]", message, flags=re.IGNORECASE)
    return _normalize_priority(match.group(0)) if match else None


def _source_payload(
    output: dict[str, Any],
    status_summary: str,
    planning_bias: str,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(existing.get("source_payload") or {}) if existing else {}
    payload.update(
        {
            "priority": output.get("priority") or payload.get("priority") or "P2",
            "role": payload.get("role") or _role_for_priority(output.get("priority")),
            "name": output.get("project_name") or payload.get("name") or "",
            "progress": status_summary,
            "planning_bias": planning_bias,
            "target_goal": output.get("target_goal") or payload.get("target_goal") or "",
            "today_goal": output.get("today_goal") or payload.get("today_goal") or "",
            "project_state_patch": output.get("project_state_patch") or payload.get("project_state_patch") or {},
        }
    )
    return payload


def _normalize_project_state_patch(
    raw_patch: Any,
    *,
    status_summary: str,
    planning_bias: str,
    target_goal: str,
    today_goal: str,
    completion_summary: str,
    action: str,
) -> dict[str, Any]:
    patch = dict(raw_patch) if isinstance(raw_patch, dict) else {}
    if action == "complete_project" and completion_summary and not str(patch.get("summary") or "").strip():
        patch["summary"] = completion_summary
    elif status_summary and not str(patch.get("summary") or "").strip():
        patch["summary"] = status_summary
    if planning_bias and not str(patch.get("planning_guidance") or "").strip():
        patch["planning_guidance"] = planning_bias
    if target_goal and not str(patch.get("target_goal") or "").strip():
        patch["target_goal"] = target_goal
    if today_goal and not str(patch.get("today_goal") or "").strip():
        patch["today_goal"] = today_goal
    facts = patch.get("facts")
    if not isinstance(facts, list):
        facts = []
    normalized_facts: list[dict[str, Any]] = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        fact = {
            "type": str(item.get("type") or "context").strip(),
            "text": str(item.get("text") or item.get("summary") or "").strip(),
        }
        if fact["text"]:
            normalized_facts.append(fact)
    patch["facts"] = normalized_facts
    return patch


def _target_goal_from_project(project: dict[str, Any]) -> str:
    return repo.project_target_goal(project)


def _today_goal_from_project(project: dict[str, Any]) -> str:
    return repo.project_today_goal(project)


def _project_payload(project: dict[str, Any] | None) -> dict[str, Any] | None:
    if project is None:
        return None
    return {
        "id": project["id"],
        "name": project["name"],
        "priority": project["priority"],
        "role": project.get("role"),
        "status": project["status"],
        "status_summary": project.get("status_summary") or "",
        "planning_bias": project.get("planning_bias") or "",
        "target_goal": repo.project_target_goal(project),
        "today_goal": repo.project_today_goal(project),
    }


def _priority_order_for_projects(projects: list[dict[str, Any]]) -> list[str]:
    values = [priority for priority in ["P0", "P1", "P2"] if any(project.get("priority") == priority for project in projects)]
    return values or ["P2"]


def _priority_preference_text(projects: list[dict[str, Any]]) -> str:
    order = _priority_order_for_projects(projects)
    if len(order) <= 1:
        return f"{order[0]} 项目优先"
    return "，".join(f"{left} 项目优先于 {right}" for left, right in zip(order, order[1:]))


def _role_for_priority(priority: Any) -> str:
    return {"P0": "主线", "P1": "推进", "P2": "维护/学习"}.get(str(priority or "P2"), "维护/学习")


def _valid_priority(value: Any) -> str:
    return _normalize_priority(value) or "P2"


def _normalize_priority(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in PRIORITY_VALUES else None


def _normalize_today_goal_policy(value: Any, action: str) -> str:
    text = str(value or "").strip().lower()
    if text in TODAY_GOAL_POLICY_VALUES:
        return text
    if action == "create_project":
        return "create"
    if action in {"complete_project", "delete_project"}:
        return "remove"
    return "keep"


def _priority_rank(value: Any) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(str(value or "P2"), 2)


def _planning_bias_from_target(target: str) -> str:
    target = str(target or "").strip()
    if target:
        return f"优先安排围绕“{target}”的最小可交付切片，留下文件、代码、笔记或决策记录。"
    return "优先安排实现方案比较、数据结构设计、最小样例和可验证实验记录。"


def _default_planning_bias(output: dict[str, Any]) -> str:
    return _planning_bias_from_target(
        output.get("target_goal") or output.get("today_goal") or output.get("status_summary") or output.get("project_name")
    )


def _default_planning_bias_for_project(project: dict[str, Any]) -> str:
    return _planning_bias_from_target(project.get("name") or "")


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


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
