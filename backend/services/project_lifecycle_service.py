from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH


PROMPT_VERSION_MOCK = "project_lifecycle_v1_mock"
PROMPT_VERSION_DEEPSEEK = "project_lifecycle_v1_deepseek"
MOCK_MODEL_NAME = "mock-project-lifecycle-adapter"
PROJECT_BACKUP_DIR = Path(__file__).resolve().parents[2] / "data" / "backups"

ACTION_VALUES = {"create_project", "complete_project", "update_project", "no_change"}
PRIORITY_VALUES = {"P0", "P1", "P2"}


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
            validator=lambda output: _validate_lifecycle_output(output, context),
            settings=settings,
            soul_path=soul_path,
        )
        output = _normalize_lifecycle_output(llm_result.output)
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
        )
    except Exception as exc:  # noqa: BLE001 - return a concise failure to the UI
        return ProjectLifecycleResult(
            {
                "status": "failed",
                "reason": _safe_error(exc),
            }
        )
    return ProjectLifecycleResult(payload)


class MockProjectLifecycleAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        message = context["message"]
        create_output = _fallback_create_output(message)
        if create_output is not None:
            return create_output
        complete_output = _fallback_complete_output(message, context["all_projects"])
        if complete_output is not None:
            return complete_output
        raise ProjectLifecycleValidationError("fallback_could_not_parse_project_lifecycle_message")


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
        "task": "Parse the user message into one project lifecycle action.",
        "required_json_fields": [
            "action",
            "project_name",
            "project_id",
            "priority",
            "status_summary",
            "planning_bias",
            "confidence",
            "reason",
        ],
        "actions": ["create_project", "complete_project", "update_project", "no_change"],
        "rules": [
            "Use create_project for a new project.",
            "Use complete_project when the user says an existing project is finished.",
            "Use update_project when the user updates a project's current progress, goal, or priority.",
            "Use no_change only when the message is not about project lifecycle.",
            "For create_project, default priority to P2 unless the user says P0, P1, or P2.",
            "For complete_project or update_project, choose an existing project_id when possible.",
            "Use concise Chinese for status_summary and planning_bias.",
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


def _validate_lifecycle_output(output: dict[str, Any], context: dict[str, Any]) -> None:
    normalized = _normalize_lifecycle_output(output)
    action = normalized["action"]
    if action == "create_project" and not normalized["project_name"]:
        raise ValueError("missing_project_name")
    if action in {"complete_project", "update_project"}:
        _resolve_project(context["all_projects"], normalized)


def _normalize_lifecycle_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("project_lifecycle_output_not_object")
    action = str(output.get("action") or "").strip()
    if action not in ACTION_VALUES:
        raise ValueError("invalid_project_lifecycle_action")
    priority = _normalize_priority(output.get("priority"))
    if action == "create_project" and priority is None:
        priority = "P2"
    confidence = _clamp_float(output.get("confidence"), 0.0, 1.0)
    project_id = _safe_positive_int(output.get("project_id"))
    return {
        "action": action,
        "project_id": project_id,
        "project_name": str(output.get("project_name") or "").strip(),
        "priority": priority,
        "status_summary": str(output.get("status_summary") or output.get("progress") or "").strip(),
        "planning_bias": str(output.get("planning_bias") or "").strip(),
        "target_goal": str(output.get("target_goal") or "").strip(),
        "completion_summary": str(output.get("completion_summary") or "").strip(),
        "confidence": confidence,
        "reason": str(output.get("reason") or "").strip(),
    }


def _apply_lifecycle_output(
    db_path: str | Path,
    *,
    message: str,
    output: dict[str, Any],
    llm_metadata: dict[str, Any],
    soul_path: Path,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        with connection:
            if output["action"] == "create_project":
                result = _apply_create_project(connection, output)
            elif output["action"] == "complete_project":
                result = _apply_complete_project(connection, output)
            elif output["action"] == "update_project":
                result = _apply_update_project(connection, output)
            else:
                result = _apply_no_change(connection, output)

            _sync_user_profile_projects(connection)
            event_id = repo.create_project_lifecycle_event(
                connection,
                raw_message=message,
                action=result["event_action"],
                project_id=result.get("project_id"),
                project_name=result.get("project_name") or output.get("project_name"),
                priority=result.get("priority") or output.get("priority"),
                previous_status=result.get("previous_status"),
                new_status=result.get("new_status"),
                previous_status_summary=result.get("previous_status_summary"),
                new_status_summary=result.get("new_status_summary"),
                planning_bias=result.get("planning_bias") or output.get("planning_bias"),
                confidence=output.get("confidence"),
                applied=1 if result.get("applied") else 0,
                reason=output.get("reason"),
                llm_metadata=llm_metadata,
                raw_output=output,
            )
            active_projects = repo.list_projects(connection)

        soul_backup: Path | None = None
        soul_sync_error: str | None = None
        soul_sync_retry_job_id: int | None = None
        if result.get("applied"):
            try:
                soul_backup = _sync_soul_current_projects(active_projects, soul_path)
            except Exception as exc:  # noqa: BLE001 - project DB state is the source of truth
                soul_sync_error = _safe_error(exc)
                from backend.services.soul_sync_service import enqueue_soul_sync_retry

                soul_sync_retry_job_id = enqueue_soul_sync_retry(
                    db_path,
                    job_type="project_lifecycle",
                    source_table="project_lifecycle_events",
                    source_id=event_id,
                    payload={
                        "project_lifecycle_event_id": event_id,
                        "action": result["event_action"],
                    },
                    error=soul_sync_error,
                )
        project = _project_payload(result.get("project"))
        return {
            "status": "applied" if result.get("applied") else "no_change",
            "action": result["event_action"],
            "project": project,
            "project_lifecycle_event_id": event_id,
            "soul_synced": bool(soul_backup) if result.get("applied") else False,
            "soul_backup": str(soul_backup) if soul_backup else None,
            "fallback_reason": llm_metadata.get("fallback_reason"),
            "soul_sync_queued": soul_sync_retry_job_id is not None,
            "soul_sync_retry_job_id": soul_sync_retry_job_id,
            "soul_sync_error": soul_sync_error,
            "message": result.get("message") or "项目信息已更新。",
        }
    finally:
        connection.close()


def _apply_create_project(connection: sqlite3.Connection, output: dict[str, Any]) -> dict[str, Any]:
    project_name = output["project_name"]
    existing = repo.get_project_by_name(connection, project_name)
    status_summary = output["status_summary"]
    planning_bias = output["planning_bias"] or _default_planning_bias(output)
    source_payload = _source_payload(output, status_summary, planning_bias)
    if existing is not None:
        previous_summary = str(existing.get("status_summary") or "")
        updated = repo.update_project(
            connection,
            int(existing["id"]),
            priority=output["priority"],
            role=existing.get("role") or _role_for_priority(output["priority"]),
            status="active",
            status_summary=status_summary or previous_summary,
            planning_bias=planning_bias,
            source_payload=source_payload,
        )
        return {
            "applied": True,
            "event_action": "update_project",
            "project": updated,
            "project_id": updated["id"] if updated else existing["id"],
            "project_name": project_name,
            "priority": output["priority"],
            "previous_status": existing.get("status"),
            "new_status": "active",
            "previous_status_summary": previous_summary,
            "new_status_summary": (updated or existing).get("status_summary"),
            "planning_bias": planning_bias,
            "message": "项目已存在，已更新为当前项目。",
        }

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
        "previous_status": None,
        "new_status": "active",
        "previous_status_summary": None,
        "new_status_summary": status_summary,
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
    previous_summary = str(project.get("status_summary") or "")
    status_summary = output["status_summary"] or previous_summary
    planning_bias = output["planning_bias"] or str(project.get("planning_bias") or "")
    updated = repo.update_project(
        connection,
        int(project["id"]),
        priority=output["priority"] or project.get("priority") or "P2",
        status="active" if project.get("status") == "completed" else project.get("status"),
        status_summary=status_summary,
        planning_bias=planning_bias,
        source_payload=_source_payload(output, status_summary, planning_bias, existing=project),
    )
    return {
        "applied": True,
        "event_action": "update_project",
        "project": updated,
        "project_id": project["id"],
        "project_name": project["name"],
        "priority": (updated or project).get("priority"),
        "previous_status": project.get("status"),
        "new_status": (updated or project).get("status"),
        "previous_status_summary": previous_summary,
        "new_status_summary": status_summary,
        "planning_bias": planning_bias,
        "message": "项目信息已更新。",
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
    profile = repo.get_user_profile(connection)
    if profile is None:
        return
    active_projects = repo.list_projects(connection)
    preferences = dict(profile.get("goal_preferences") or {})
    preferences["project_priorities"] = [
        {
            "priority": project["priority"],
            "role": project.get("role") or _role_for_priority(project.get("priority")),
            "name": project["name"],
            "progress": project.get("status_summary") or "",
            "planning_bias": project.get("planning_bias") or "",
            "id": project["id"],
        }
        for project in active_projects
    ]
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
        lines.append(f"{index}. {project['name']}。")
    lines.extend(
        [
            "",
            "项目的当前进度、阶段变化、最近阻塞和临时优先级以数据库中的结构化记录为准，不写入 SOUL.md。SOUL.md 只保留长期稳定原则、项目边界和目标生成纪律。",
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
    project_name = _first_segment(remainder, ["当前进度", "进度", "目标", "。", "\n"])
    if not project_name:
        return None
    progress = _field_segment(message, ["当前进度", "进度"]) or ""
    target = _field_segment(message, ["目标"]) or ""
    return {
        "action": "create_project",
        "project_id": None,
        "project_name": project_name,
        "priority": priority,
        "status_summary": progress,
        "planning_bias": _planning_bias_from_target(target),
        "target_goal": target,
        "completion_summary": "",
        "confidence": 0.55,
        "reason": "mock fallback parsed an explicit create project message.",
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
                "completion_summary": _completion_summary(message, name),
                "confidence": 0.55,
                "reason": "mock fallback parsed an explicit complete project message.",
            }
    return None


def _field_segment(message: str, labels: list[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*(.+)", message, flags=re.DOTALL)
        if match:
            return _first_segment(match.group(1), ["。", "；", "\n"])
    return None


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
        }
    )
    return payload


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


def _priority_rank(value: Any) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(str(value or "P2"), 2)


def _planning_bias_from_target(target: str) -> str:
    target = str(target or "").strip()
    if target:
        return f"优先安排围绕“{target}”的最小可交付切片，留下文件、代码、笔记或决策记录。"
    return "优先安排实现方案比较、数据结构设计、最小样例和可验证实验记录。"


def _default_planning_bias(output: dict[str, Any]) -> str:
    return _planning_bias_from_target(output.get("target_goal") or output.get("status_summary") or output.get("project_name"))


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
