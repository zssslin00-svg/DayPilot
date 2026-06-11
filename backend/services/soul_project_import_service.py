from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.project_lifecycle_service import apply_project_lifecycle_output
from backend.services.soul_context import SOUL_PATH


SECTION_TITLE = "## 当前项目"
NEXT_SECTION_PATTERN = re.compile(r"^##\s+", flags=re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|(?:\d+)[.)、])\s+(.+?)\s*$")
STOP_LINES = ("每日生成规则", "项目的当前进度")
EMPTY_PROJECT_MARKERS = ("暂无 active 项目", "暂无当前项目", "当前 active 项目有 0 个")
PRIORITY_VALUES = {"P0", "P1", "P2"}


@dataclass(frozen=True)
class SoulProjectImportResult:
    payload: dict[str, Any]


@dataclass(frozen=True)
class SoulProjectEntry:
    position: int
    name: str
    priority: str | None
    status_summary: str
    target_goal: str
    planning_bias: str
    raw_text: str


class SoulProjectImportError(ValueError):
    """Raised when SOUL.md cannot be imported safely."""


def import_current_projects_from_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    soul_path: str | Path = SOUL_PATH,
    today: date | None = None,
) -> SoulProjectImportResult:
    path = Path(soul_path)
    section_text = _extract_current_projects_section(path)
    entries = _parse_project_entries(section_text)
    if not entries and not _section_declares_no_active_projects(section_text):
        raise SoulProjectImportError("SOUL.md 当前项目段落没有可识别的项目列表。")

    _ensure_unique_names(entries)
    section_hash = _hash_text(section_text)
    connection = initialize_database(db_path)
    try:
        previous_state = repo.get_soul_project_import_state(connection)
        active_projects = repo.list_projects(connection)
        all_projects = repo.list_projects(connection, include_archived=True)
    finally:
        connection.close()

    previous_snapshot = previous_state.get("snapshot") if previous_state else {}
    lifecycle_output, planned_actions = _build_lifecycle_output(
        entries,
        active_projects=active_projects,
        all_projects=all_projects,
        previous_snapshot=previous_snapshot if isinstance(previous_snapshot, dict) else {},
    )
    message = _import_message(section_hash)
    if lifecycle_output["items"]:
        lifecycle_payload = apply_project_lifecycle_output(
            db_path,
            message=message,
            output=lifecycle_output,
            llm_metadata={
                "prompt_version": "soul_project_import_v1",
                "model_name": "deterministic-soul-project-importer",
                "llm_mode_used": "deterministic",
                "source": "SOUL.md",
                "section_hash": section_hash,
            },
            soul_path=path,
            today=today,
        ).payload
    else:
        lifecycle_payload = {
            "status": "no_change",
            "action": "soul_project_import",
            "items": [],
            "applied_count": 0,
            "failed_count": 0,
            "today_goal_refresh_failed_count": 0,
            "soul_synced": False,
            "soul_backup": None,
            "soul_sync_queued": False,
            "soul_sync_retry_job_id": None,
            "soul_sync_error": None,
            "message": "SOUL.md 当前项目没有变化。",
        }

    final_section_text = _read_current_section_if_possible(path, fallback=section_text)
    final_snapshot = _persist_import_state(
        db_path,
        section_text=final_section_text,
        section_hash=_hash_text(final_section_text),
    )
    counts = _action_counts(planned_actions, lifecycle_payload.get("items") or [])
    payload = {
        "status": _import_status(lifecycle_payload),
        "source": "SOUL.md",
        "section_hash": section_hash,
        "parsed_project_count": len(entries),
        "created_count": counts["create_project"],
        "updated_count": counts["update_project"],
        "renamed_count": counts["rename_project"],
        "completed_count": counts["complete_project"],
        "no_change_count": len(entries) - counts["parsed_changed_count"],
        "items": lifecycle_payload.get("items") or [],
        "lifecycle": lifecycle_payload,
        "active_projects": final_snapshot["projects"],
        "message": _result_message(counts, lifecycle_payload),
    }
    return SoulProjectImportResult(payload)


def _extract_current_projects_section(soul_path: Path) -> str:
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SoulProjectImportError("SOUL.md 不存在。") from exc

    start = text.find(SECTION_TITLE)
    if start < 0:
        raise SoulProjectImportError("SOUL.md 缺少 ## 当前项目 段落。")
    next_match = NEXT_SECTION_PATTERN.search(text, start + len(SECTION_TITLE))
    end = next_match.start() if next_match else len(text)
    return text[start:end].strip()


def _read_current_section_if_possible(soul_path: Path, *, fallback: str) -> str:
    try:
        return _extract_current_projects_section(soul_path)
    except SoulProjectImportError:
        return fallback


def _parse_project_entries(section_text: str) -> list[SoulProjectEntry]:
    entries: list[SoulProjectEntry] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(marker) for marker in STOP_LINES):
            break
        match = LIST_ITEM_PATTERN.match(stripped)
        if match is None:
            continue
        raw_text = match.group(1).strip()
        if _looks_like_generation_rule(raw_text):
            continue
        entry = _parse_project_entry(raw_text, position=len(entries) + 1)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_project_entry(raw_text: str, *, position: int) -> SoulProjectEntry | None:
    priority = _extract_priority(raw_text)
    text = _strip_priority(raw_text)
    text = re.sub(r"^(?:新增|添加|创建)\s*(?:P[012]\s*)?项目\s*[:：]?\s*", "", text, flags=re.IGNORECASE)
    name, details = _split_name_and_details(text)
    name = _clean_project_name(name)
    if not name:
        return None

    status_summary = _field_segment(details, ["当前进度", "进度", "阶段", "最近阻塞"]) or ""
    target_goal = _field_segment(details, ["目标", "本周目标", "希望推进到"]) or ""
    if not status_summary and details and not _starts_with_target_label(details):
        status_summary = _compact_text(details, 240)
    planning_bias = _planning_bias_from_target(target_goal) if target_goal else ""
    return SoulProjectEntry(
        position=position,
        name=name,
        priority=priority,
        status_summary=status_summary,
        target_goal=target_goal,
        planning_bias=planning_bias,
        raw_text=raw_text,
    )


def _split_name_and_details(text: str) -> tuple[str, str]:
    detail_index = _first_detail_label_index(text)
    colon_index = _first_colon_index(text)
    dash_index = text.find(" - ")
    if colon_index > 0 and (detail_index < 0 or colon_index < detail_index):
        return text[:colon_index], text[colon_index + 1 :]
    if detail_index > 0:
        return text[:detail_index], text[detail_index:]
    if dash_index > 0:
        return text[:dash_index], text[dash_index + 3 :]
    return text, ""


def _first_detail_label_index(text: str) -> int:
    indices = [
        index
        for label in ["当前进度", "进度", "阶段", "最近阻塞", "目标", "本周目标", "希望推进到"]
        for index in [text.find(label)]
        if index >= 0
    ]
    return min(indices) if indices else -1


def _first_colon_index(text: str) -> int:
    indices = [index for index in (text.find("："), text.find(":")) if index >= 0]
    return min(indices) if indices else -1


def _field_segment(text: str, labels: list[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*(?:[:：]|是)?\s*(.+)", text, flags=re.DOTALL)
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


def _extract_priority(text: str) -> str | None:
    match = re.search(r"(?:^|[\s\[（(【])(?P<priority>P[012])(?:$|[\s\]）)】:：、-])", text, flags=re.IGNORECASE)
    if match is None:
        return None
    priority = match.group("priority").upper()
    return priority if priority in PRIORITY_VALUES else None


def _strip_priority(text: str) -> str:
    return re.sub(r"^\s*[\[（(【]?\s*P[012]\s*[\]）)】]?\s*[:：、-]?\s*", "", text, flags=re.IGNORECASE).strip()


def _clean_project_name(text: str) -> str:
    name = text.strip(" ：:，,；;。-")
    name = re.sub(r"^(?:项目|当前项目)\s*[:：]?\s*", "", name)
    return _compact_text(name, 120)


def _compact_text(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit].strip()


def _starts_with_target_label(text: str) -> bool:
    stripped = text.strip()
    return any(stripped.startswith(label) for label in ("目标", "本周目标", "希望推进到"))


def _looks_like_generation_rule(text: str) -> bool:
    return any(
        token in text
        for token in (
            "每个 active 项目",
            "昨日未完成",
            "昨日显式完成",
            "目标必须",
            "每个项目都",
        )
    )


def _section_declares_no_active_projects(section_text: str) -> bool:
    return any(marker in section_text for marker in EMPTY_PROJECT_MARKERS)


def _ensure_unique_names(entries: list[SoulProjectEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        key = _name_key(entry.name)
        if key in seen:
            raise SoulProjectImportError(f"SOUL.md 当前项目包含重复项目名：{entry.name}")
        seen.add(key)


def _build_lifecycle_output(
    entries: list[SoulProjectEntry],
    *,
    active_projects: list[dict[str, Any]],
    all_projects: list[dict[str, Any]],
    previous_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    active_by_name = {_name_key(project["name"]): project for project in active_projects}
    all_by_name = {_name_key(project["name"]): project for project in all_projects}
    active_by_id = {int(project["id"]): project for project in active_projects}
    active_by_position = {index: project for index, project in enumerate(active_projects, start=1)}
    previous_by_position = _previous_projects_by_position(previous_snapshot)
    parsed_name_keys = {_name_key(entry.name) for entry in entries}
    matched_active_ids: set[int] = set()
    items: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []

    for entry in entries:
        existing = active_by_name.get(_name_key(entry.name)) or all_by_name.get(_name_key(entry.name))
        renamed = False
        if existing is None:
            existing = _rename_candidate(
                entry,
                active_by_id=active_by_id,
                active_by_position=active_by_position,
                previous_by_position=previous_by_position,
                parsed_name_keys=parsed_name_keys,
                matched_active_ids=matched_active_ids,
            )
            renamed = existing is not None

        if existing is None:
            items.append(_create_item(entry))
            planned.append({"kind": "create_project", "entry": entry.name})
            continue

        project_id = int(existing["id"])
        if str(existing.get("status") or "") == "active":
            matched_active_ids.add(project_id)
        if renamed or _entry_changes_project(entry, existing):
            items.append(_update_item(entry, existing, renamed=renamed))
            planned.append({"kind": "rename_project" if renamed else "update_project", "entry": entry.name})

    for project in active_projects:
        project_id = int(project["id"])
        if project_id in matched_active_ids or _name_key(project["name"]) in parsed_name_keys:
            continue
        items.append(_complete_item(project))
        planned.append({"kind": "complete_project", "entry": project["name"]})

    return {
        "schema_version": "project_lifecycle_batch.v1",
        "items": items,
        "confidence": 1.0,
        "reason": "Imported deterministic project lifecycle changes from SOUL.md.",
    }, planned


def _rename_candidate(
    entry: SoulProjectEntry,
    *,
    active_by_id: dict[int, dict[str, Any]],
    active_by_position: dict[int, dict[str, Any]],
    previous_by_position: dict[int, dict[str, Any]],
    parsed_name_keys: set[str],
    matched_active_ids: set[int],
) -> dict[str, Any] | None:
    previous = previous_by_position.get(entry.position)
    if previous is not None:
        project_id = _safe_int(previous.get("project_id"))
        candidate = active_by_id.get(project_id) if project_id is not None else None
        if candidate is not None and _can_rename_candidate(candidate, parsed_name_keys, matched_active_ids):
            return candidate

    candidate = active_by_position.get(entry.position)
    if candidate is not None and _can_rename_candidate(candidate, parsed_name_keys, matched_active_ids):
        return candidate
    return None


def _can_rename_candidate(
    project: dict[str, Any],
    parsed_name_keys: set[str],
    matched_active_ids: set[int],
) -> bool:
    return int(project["id"]) not in matched_active_ids and _name_key(project["name"]) not in parsed_name_keys


def _entry_changes_project(entry: SoulProjectEntry, project: dict[str, Any]) -> bool:
    if str(project.get("status") or "") != "active":
        return True
    if entry.priority and entry.priority != str(project.get("priority") or "P2"):
        return True
    if entry.status_summary and entry.status_summary != str(project.get("status_summary") or ""):
        return True
    if entry.target_goal and entry.target_goal != repo.project_target_goal(project):
        return True
    if entry.planning_bias and entry.planning_bias != str(project.get("planning_bias") or ""):
        return True
    return False


def _create_item(entry: SoulProjectEntry) -> dict[str, Any]:
    priority = entry.priority or "P2"
    return {
        "action": "create_project",
        "project_id": None,
        "project_name": entry.name,
        "priority": priority,
        "status_summary": entry.status_summary,
        "planning_bias": entry.planning_bias,
        "target_goal": entry.target_goal,
        "project_state_patch": _project_state_patch(entry),
        "completion_summary": "",
        "today_goal_policy": "create",
        "confidence": 1.0,
        "reason": "SOUL.md 当前项目列表新增项目。",
    }


def _update_item(entry: SoulProjectEntry, project: dict[str, Any], *, renamed: bool) -> dict[str, Any]:
    target_goal = entry.target_goal or repo.project_target_goal(project)
    planning_bias = entry.planning_bias or str(project.get("planning_bias") or "")
    return {
        "action": "update_project",
        "project_id": int(project["id"]),
        "project_name": entry.name,
        "priority": entry.priority or project.get("priority") or "P2",
        "status_summary": entry.status_summary or str(project.get("status_summary") or ""),
        "planning_bias": planning_bias,
        "target_goal": target_goal,
        "project_state_patch": _project_state_patch(entry, fallback_project=project),
        "completion_summary": "",
        "today_goal_policy": "refresh",
        "confidence": 1.0,
        "reason": "SOUL.md 当前项目列表改名。" if renamed else "SOUL.md 当前项目列表更新项目状态。",
    }


def _complete_item(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "complete_project",
        "project_id": int(project["id"]),
        "project_name": project["name"],
        "priority": project.get("priority") or "P2",
        "status_summary": str(project.get("status_summary") or ""),
        "planning_bias": str(project.get("planning_bias") or ""),
        "target_goal": repo.project_target_goal(project),
        "project_state_patch": {},
        "completion_summary": "从 SOUL.md 当前项目列表移除，标记为完成。",
        "today_goal_policy": "remove",
        "confidence": 1.0,
        "reason": "项目不再出现在 SOUL.md 当前项目列表中。",
    }


def _project_state_patch(
    entry: SoulProjectEntry,
    *,
    fallback_project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "summary": entry.status_summary or (str(fallback_project.get("status_summary") or "") if fallback_project else ""),
        "planning_guidance": entry.planning_bias
        or (str(fallback_project.get("planning_bias") or "") if fallback_project else ""),
        "target_goal": entry.target_goal or (repo.project_target_goal(fallback_project) if fallback_project else ""),
        "facts": [],
        "updated_from": {
            "source": "soul_project_import",
            "position": entry.position,
            "raw_text": entry.raw_text,
        },
    }


def _previous_projects_by_position(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    projects = snapshot.get("projects")
    if not isinstance(projects, list):
        return {}
    indexed: dict[int, dict[str, Any]] = {}
    for item in projects:
        if not isinstance(item, dict):
            continue
        position = _safe_int(item.get("position"))
        if position is not None and position > 0:
            indexed[position] = item
    return indexed


def _persist_import_state(
    db_path: str | Path,
    *,
    section_text: str,
    section_hash: str,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        with connection:
            projects = repo.list_projects(connection)
            snapshot = _snapshot_from_projects(projects)
            repo.upsert_soul_project_import_state(
                connection,
                section_hash=section_hash,
                section_text=section_text,
                snapshot=snapshot,
                last_imported_at=_now_text(),
            )
            return snapshot
    finally:
        connection.close()


def _snapshot_from_projects(projects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "soul_project_import_state.v1",
        "projects": [
            {
                "position": index,
                "project_id": int(project["id"]),
                "name": project["name"],
                "priority": project.get("priority") or "P2",
                "project_state_hash": repo.project_state_hash(project),
            }
            for index, project in enumerate(projects, start=1)
        ],
    }


def _action_counts(planned: list[dict[str, Any]], lifecycle_items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "create_project": 0,
        "update_project": 0,
        "rename_project": 0,
        "complete_project": 0,
        "parsed_changed_count": 0,
    }
    for plan, item in zip(planned, lifecycle_items):
        if item.get("status") != "applied":
            continue
        kind = str(plan.get("kind") or "")
        if kind in counts:
            counts[kind] += 1
        if kind in {"create_project", "update_project", "rename_project"}:
            counts["parsed_changed_count"] += 1
    return counts


def _import_status(lifecycle_payload: dict[str, Any]) -> str:
    status = str(lifecycle_payload.get("status") or "no_change")
    return status if status in {"applied", "partial", "failed"} else "no_change"


def _result_message(counts: dict[str, int], lifecycle_payload: dict[str, Any]) -> str:
    if lifecycle_payload.get("status") == "failed":
        return lifecycle_payload.get("message") or "SOUL.md 项目同步失败。"
    changed = counts["create_project"] + counts["update_project"] + counts["rename_project"] + counts["complete_project"]
    if not changed:
        return "SOUL.md 当前项目没有变化。"
    return (
        "SOUL.md 当前项目已同步："
        f"新增 {counts['create_project']}，"
        f"更新 {counts['update_project']}，"
        f"改名 {counts['rename_project']}，"
        f"完成 {counts['complete_project']}。"
    )


def _import_message(section_hash: str) -> str:
    return f"从 SOUL.md 当前项目段落导入项目，section_hash={section_hash}"


def _planning_bias_from_target(target: str) -> str:
    target = str(target or "").strip()
    if not target:
        return ""
    return f"优先安排围绕“{target}”的最小可交付切片，留下文件、代码、笔记或决策记录。"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _name_key(name: Any) -> str:
    return str(name or "").strip().casefold()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
