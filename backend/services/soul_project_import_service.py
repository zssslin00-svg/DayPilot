from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import default_backup_dir
from backend.config.settings import DayPilotSettings, load_daypilot_settings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.llm_client import generate_json_with_fallback
from backend.services.project_lifecycle_service import apply_project_lifecycle_output
from backend.services.soul_context import SOUL_PATH


SECTION_TITLE = "## 当前项目"
PROMPT_VERSION_MOCK = "soul_project_import_v2_mock"
PROMPT_VERSION_DEEPSEEK = "soul_project_import_v2_deepseek"
MOCK_MODEL_NAME = "mock-soul-project-parser"
NEXT_SECTION_PATTERN = re.compile(r"^##\s+", flags=re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|(?:\d+)[.)、])\s+(.+?)\s*$")
STOP_LINES = ("本段落由 DayPilot 管理", "本段落是项目变更", "每日生成规则", "项目的当前进度")
EMPTY_PROJECT_MARKERS = ("暂无 active 项目", "暂无当前项目", "当前 active 项目有 0 个")
PRIORITY_VALUES = {"P0", "P1", "P2"}
ACTIVE_COUNT_PATTERN = re.compile(r"当前\s*active\s*项目(?:有|共|数量)?\s*[:：]?\s*\d+\s*个", flags=re.IGNORECASE)
PROJECT_IMPORT_BACKUP_DIR = default_backup_dir()


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
    today_goal: str
    planning_bias: str
    raw_text: str


class SoulProjectImportError(ValueError):
    """Raised when SOUL.md cannot be imported safely."""


def import_current_projects_from_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    soul_path: str | Path = SOUL_PATH,
    today: date | None = None,
    settings: DayPilotSettings | None = None,
) -> SoulProjectImportResult:
    resolved_settings = settings or load_daypilot_settings()
    path = Path(soul_path)
    section_text = _extract_current_projects_section(path)
    project_parse_text = _project_parse_region(section_text)
    parse_mode = "none"
    entries: list[SoulProjectEntry] = []
    declares_no_active_projects = _section_declares_no_active_projects(section_text)
    if declares_no_active_projects:
        parse_mode = "explicit_empty"
    if not declares_no_active_projects and _should_use_llm_project_parser(resolved_settings):
        entries = _parse_project_entries_with_llm(
            project_parse_text,
            soul_path=path,
            settings=resolved_settings,
        )
        if entries:
            parse_mode = "deepseek"
    if not entries:
        entries = _parse_project_entries(project_parse_text)
        if entries:
            parse_mode = "deterministic"
    section_hash = _hash_text(section_text)
    connection = initialize_database(db_path)
    try:
        previous_state = repo.get_soul_project_import_state(connection)
        active_projects = repo.list_projects(connection)
        all_projects = repo.list_projects(connection, include_archived=True)
    finally:
        connection.close()

    previous_snapshot = previous_state.get("snapshot") if previous_state else {}
    if not isinstance(previous_snapshot, dict):
        previous_snapshot = {}

    if not entries and not declares_no_active_projects:
        raise SoulProjectImportError("SOUL.md 当前项目段落没有可识别的项目列表。")

    _ensure_unique_names(entries)

    lifecycle_output, planned_actions = _build_lifecycle_output(
        entries,
        active_projects=active_projects,
        all_projects=all_projects,
        previous_snapshot=previous_snapshot,
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
            sync_soul=False,
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
    if declares_no_active_projects:
        _sync_no_active_project_preferences(db_path)

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
        "target": "frontend",
        "direction": "soul_to_frontend",
        "parse_mode": parse_mode,
        "section_hash": section_hash,
        "parsed_project_count": len(entries),
        "soul_patched_count": 0,
        "soul_patched_project_names": [],
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


def _sync_no_active_project_preferences(db_path: str | Path) -> None:
    connection = initialize_database(db_path)
    try:
        with connection:
            profile = repo.get_user_profile(connection)
            if profile is None:
                repo.create_user_profile(
                    connection,
                    id=1,
                    long_term_direction="当前没有 active 项目，项目状态由 SOUL.md 管理。",
                    current_focus_projects=[],
                    goal_preferences={"project_priorities": []},
                )
                return
            preferences = dict(profile.get("goal_preferences") or {})
            preferences["project_priorities"] = []
            repo.update_user_profile(
                connection,
                int(profile["id"]),
                current_focus_projects=[],
                goal_preferences=preferences,
            )
    finally:
        connection.close()


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


def _project_parse_region(section_text: str) -> str:
    lines: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped == SECTION_TITLE:
            continue
        if any(stripped.startswith(marker) for marker in STOP_LINES):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _parse_project_entries(section_text: str) -> list[SoulProjectEntry]:
    entries: list[SoulProjectEntry] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(marker) for marker in STOP_LINES):
            break
        match = LIST_ITEM_PATTERN.match(stripped)
        if match is not None:
            raw_text = match.group(1).strip()
        elif _looks_like_project_entry_line(stripped):
            raw_text = stripped
        else:
            continue
        if _looks_like_generation_rule(raw_text):
            continue
        entry = _parse_project_entry(raw_text, position=len(entries) + 1)
        if entry is not None:
            entries.append(entry)
    return entries


def _looks_like_project_entry_line(text: str) -> bool:
    if re.match(r"^\s*P[012]\b", text, flags=re.IGNORECASE):
        return True
    if re.match(r"^\s*(?:项目|当前项目)\s*[:：]", text):
        return True
    return False


def _should_use_llm_project_parser(settings: DayPilotSettings) -> bool:
    return settings.has_deepseek_key and settings.llm_mode in {"auto", "deepseek"}


def _parse_project_entries_with_llm(
    section_text: str,
    *,
    soul_path: Path,
    settings: DayPilotSettings,
) -> list[SoulProjectEntry]:
    try:
        llm_result = generate_json_with_fallback(
            task_name="soul_project_import_parse",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda _soul: _soul_project_parse_messages(section_text),
            mock_generate=lambda: _mock_soul_project_parse(section_text),
            normalizer=_normalize_soul_project_parse_output,
            validator=_validate_soul_project_parse_output,
            settings=settings,
            soul_path=soul_path,
        )
    except Exception:
        return []
    if llm_result.metadata.get("llm_mode_used") != "deepseek":
        return []
    return _entries_from_llm_output(llm_result.output)


def _soul_project_parse_messages(section_text: str) -> list[dict[str, str]]:
    payload = {
        "task": "Parse the SOUL.md current-project section into fixed DayPilot active project records.",
        "required_json_shape": {
            "projects": [
                {
                    "name": "project name",
                    "priority": "P0|P1|P2|null; infer only from clear priority words, otherwise null",
                    "status_summary": "current progress/status, empty if unstated",
                    "target_goal": "project final goal, empty if unstated",
                    "today_goal": "project today goal, empty if unstated",
                }
            ]
        },
        "rules": [
            "Return only active projects explicitly present in the section.",
            "Ignore instructions, generation rules, explanatory text, and empty-project markers.",
            "Map 项目最终目标, 最终目标, 长期目标 to target_goal.",
            "Map 项目今日目标, 今日目标, 今天目标 to today_goal.",
            "For legacy 目标 without a clearer final/today label, put the same value in both target_goal and today_goal.",
            "Do not invent missing projects or goals.",
            "Priority is optional and hidden from the user. If the section does not clearly say a project is main/high/medium/low priority, return null.",
            "If the user says 主线, 最重要, 高优先, or 必须优先, use P0. If the user says 次要, 低优先, 维护, or 可选, use P2. Use P1 only for clear medium/secondary priority.",
        ],
        "section_text": section_text,
    }
    return [
        {
            "role": "system",
            "content": (
                "You parse DayPilot SOUL.md project text. Return exactly one valid JSON object. "
                "Do not include Markdown fences or prose."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def _mock_soul_project_parse(section_text: str) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<priority>P[012])\s*(?P<body>.*?)(?=(?:\n|\r|\s)P[012]\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(section_text):
        raw = f"{match.group('priority')} {match.group('body')}".strip()
        entry = _parse_project_entry(_compact_text(raw, 600), position=len(projects) + 1)
        if entry is None:
            continue
        projects.append(
            {
                "name": entry.name,
                "priority": entry.priority,
                "status_summary": entry.status_summary,
                "target_goal": entry.target_goal,
                "today_goal": entry.today_goal,
            }
        )
    if projects:
        return {"projects": projects}

    prose_pattern = re.compile(
        r"(?P<name>[\w A-Za-z0-9_\-\u4e00-\u9fff]{2,80}?项目)\s*(?P<body>.*?)(?=(?:[\n。；]\s*[\w A-Za-z0-9_\-\u4e00-\u9fff]{2,80}?项目)|$)",
        flags=re.DOTALL,
    )
    for match in prose_pattern.finditer(section_text):
        name = _clean_prose_project_name(match.group("name"))
        if not name or name in {"当前项目", "active 项目"}:
            continue
        raw = f"{name}：{match.group('body').strip()}"
        entry = _parse_project_entry(_compact_text(raw, 600), position=len(projects) + 1)
        if entry is None:
            continue
        projects.append(
            {
                "name": entry.name,
                "priority": entry.priority,
                "status_summary": entry.status_summary,
                "target_goal": entry.target_goal,
                "today_goal": entry.today_goal,
            }
        )
    return {"projects": projects}


def _clean_prose_project_name(text: str) -> str:
    name = re.sub(r"^.*(?:推进|跟踪|维护|处理|做)\s*", "", str(text or "").strip())
    return _clean_project_name(name)


def _normalize_soul_project_parse_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("soul_project_parse_output_not_object")
    raw_projects = output.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("soul_project_parse_projects_not_array")
    projects: list[dict[str, Any]] = []
    for item in raw_projects:
        if not isinstance(item, dict):
            raise ValueError("soul_project_parse_project_not_object")
        priority = _normalize_priority(item.get("priority"))
        target_goal = _compact_text(item.get("target_goal"), 240)
        today_goal = _compact_text(item.get("today_goal"), 240)
        legacy_goal = _compact_text(item.get("goal"), 240)
        if legacy_goal and not target_goal and not today_goal:
            target_goal = legacy_goal
            today_goal = legacy_goal
        projects.append(
            {
                "name": _clean_project_name(str(item.get("name") or "")),
                "priority": priority,
                "status_summary": _compact_text(item.get("status_summary") or item.get("progress"), 240),
                "target_goal": target_goal,
                "today_goal": today_goal,
            }
        )
    return {"projects": projects}


def _validate_soul_project_parse_output(output: dict[str, Any]) -> None:
    projects = output.get("projects")
    if not isinstance(projects, list):
        raise ValueError("missing_projects")
    seen: set[str] = set()
    for item in projects:
        if not isinstance(item, dict):
            raise ValueError("project_not_object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("missing_project_name")
        key = _name_key(name)
        if key in seen:
            raise ValueError("duplicate_project_name")
        seen.add(key)


def _entries_from_llm_output(output: dict[str, Any]) -> list[SoulProjectEntry]:
    entries: list[SoulProjectEntry] = []
    for item in output.get("projects") or []:
        if not isinstance(item, dict):
            continue
        entry = SoulProjectEntry(
            position=len(entries) + 1,
            name=str(item.get("name") or "").strip(),
            priority=_normalize_priority(item.get("priority")),
            status_summary=_compact_text(item.get("status_summary"), 240),
            target_goal=_compact_text(item.get("target_goal"), 240),
            today_goal=_compact_text(item.get("today_goal"), 240),
            planning_bias=_planning_bias_from_target(
                _compact_text(item.get("target_goal"), 240) or _compact_text(item.get("today_goal"), 240)
            ),
            raw_text=f"llm_parse:{item}",
        )
        if entry.name:
            entries.append(entry)
    return entries


def _parse_project_entry(raw_text: str, *, position: int) -> SoulProjectEntry | None:
    priority = _extract_priority(raw_text) or _infer_priority_from_text(raw_text)
    text = _strip_priority(raw_text)
    text = re.sub(r"^(?:新增|添加|创建)\s*(?:P[012]\s*)?项目\s*[:：]?\s*", "", text, flags=re.IGNORECASE)
    name, details = _split_name_and_details(text)
    name = _clean_project_name(name)
    if not name:
        return None

    status_summary = _field_segment(details, ["当前进度", "进度", "阶段", "最近阻塞"]) or ""
    target_goal, today_goal = _extract_project_goals(details)
    if not status_summary and details and not _starts_with_target_label(details):
        status_summary = _compact_text(details, 240)
    planning_bias = _planning_bias_from_target(target_goal or today_goal) if today_goal or target_goal else ""
    return SoulProjectEntry(
        position=position,
        name=name,
        priority=priority,
        status_summary=status_summary,
        target_goal=target_goal,
        today_goal=today_goal,
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
        for label in [
            "当前进度",
            "进度",
            "阶段",
            "最近阻塞",
            "项目最终目标",
            "最终目标",
            "项目今日目标",
            "今日目标",
            "目标",
            "本周目标",
            "希望推进到",
        ]
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
            return _first_segment(match.group(1), _field_value_stops())
    return None


def _field_value_stops() -> list[str]:
    return ["。", "；", "\n", "项目最终目标", "最终目标", "项目今日目标", "今日目标"]


def _extract_project_goals(text: str) -> tuple[str, str]:
    target_goal = _field_segment(text, ["项目最终目标", "最终目标"])
    today_goal = _field_segment(text, ["项目今日目标", "今日目标"])
    if not target_goal and not today_goal:
        legacy_goal = _field_segment(text, ["目标", "本周目标", "希望推进到"]) or ""
        return legacy_goal, legacy_goal
    return target_goal or "", today_goal or ""


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
    return _normalize_priority(match.group("priority"))


def _infer_priority_from_text(text: str) -> str | None:
    value = str(text or "")
    if any(token in value for token in ("主线", "最重要", "最高优先", "高优先", "必须优先")):
        return "P0"
    if any(token in value for token in ("中优先", "中等优先", "次主线", "第二优先")):
        return "P1"
    if any(token in value for token in ("低优先", "次要", "维护", "可选")):
        return "P2"
    return None


def _normalize_priority(value: Any) -> str | None:
    priority = str(value or "").strip().upper()
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
    return any(
        stripped.startswith(label)
        for label in ("项目最终目标", "最终目标", "项目今日目标", "今日目标", "目标", "本周目标", "希望推进到")
    )


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


def _merge_frontend_active_projects_into_soul(
    soul_path: Path,
    *,
    section_text: str,
    entries: list[SoulProjectEntry],
    active_projects: list[dict[str, Any]],
    previous_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    covered_ids = _active_project_ids_covered_by_entries(
        entries,
        active_projects=active_projects,
        previous_snapshot=previous_snapshot,
    )
    missing_projects = [project for project in active_projects if int(project["id"]) not in covered_ids]
    if not missing_projects:
        return None

    backup_path = _backup_soul(soul_path)
    rendered = _merge_current_project_section_text(section_text, entries, missing_projects)
    _write_current_projects_section(soul_path, rendered)
    return {
        "count": len(missing_projects),
        "project_names": [str(project.get("name") or "") for project in missing_projects],
        "backup_path": str(backup_path),
    }


def _active_project_ids_covered_by_entries(
    entries: list[SoulProjectEntry],
    *,
    active_projects: list[dict[str, Any]],
    previous_snapshot: dict[str, Any],
) -> set[int]:
    active_by_name = {_name_key(project["name"]): project for project in active_projects}
    active_by_id = {int(project["id"]): project for project in active_projects}
    active_by_position = {index: project for index, project in enumerate(active_projects, start=1)}
    previous_by_position = _previous_projects_by_position(previous_snapshot)
    parsed_name_keys = {_name_key(entry.name) for entry in entries}
    covered_ids: set[int] = set()

    for entry in entries:
        exact = active_by_name.get(_name_key(entry.name))
        if exact is not None:
            covered_ids.add(int(exact["id"]))
            continue

        candidate = None
        previous = previous_by_position.get(entry.position)
        if previous is not None:
            project_id = _safe_int(previous.get("project_id"))
            candidate = active_by_id.get(project_id) if project_id is not None else None
        if candidate is None:
            candidate = active_by_position.get(entry.position)
        if candidate is None:
            continue
        candidate_id = int(candidate["id"])
        if candidate_id not in covered_ids and _name_key(candidate["name"]) not in parsed_name_keys:
            covered_ids.add(candidate_id)
    return covered_ids


def _merge_current_project_section_text(
    section_text: str,
    entries: list[SoulProjectEntry],
    missing_projects: list[dict[str, Any]],
) -> str:
    lines = section_text.splitlines() or [SECTION_TITLE]
    total_count = len(entries) + len(missing_projects)
    count_line_seen = False
    retained: list[str] = []
    insert_index: int | None = None

    for line in lines:
        stripped = line.strip()
        if _is_empty_project_marker_line(stripped):
            continue
        if ACTIVE_COUNT_PATTERN.search(stripped):
            retained.append(_active_project_count_line(total_count))
            count_line_seen = True
            continue
        if insert_index is None and any(stripped.startswith(marker) for marker in STOP_LINES):
            insert_index = len(retained)
        retained.append(line)

    if not count_line_seen:
        retained, insert_index = _insert_active_project_count_line(retained, total_count, insert_index)

    if insert_index is None:
        insert_index = len(retained)
    while insert_index > 0 and retained[insert_index - 1].strip() == "":
        insert_index -= 1

    missing_lines = [
        _render_frontend_project_line(len(entries) + offset, project)
        for offset, project in enumerate(missing_projects, start=1)
    ]
    next_lines = retained[:insert_index]
    if next_lines and next_lines[-1].strip() != "":
        next_lines.append("")
    next_lines.extend(missing_lines)
    tail = retained[insert_index:]
    if tail and tail[0].strip() != "":
        next_lines.append("")
    next_lines.extend(tail)
    return "\n".join(next_lines).rstrip()


def _insert_active_project_count_line(
    lines: list[str],
    total_count: int,
    insert_index: int | None,
) -> tuple[list[str], int | None]:
    count_lines = ["", _active_project_count_line(total_count), ""]
    if lines and lines[0].strip() == SECTION_TITLE:
        next_lines = lines[:1] + count_lines + lines[1:]
        if insert_index is not None and insert_index >= 1:
            insert_index += len(count_lines)
        return next_lines, insert_index
    return [SECTION_TITLE, *count_lines, *lines], (
        insert_index + len(count_lines) + 1 if insert_index is not None else None
    )


def _is_empty_project_marker_line(text: str) -> bool:
    return any(marker in text for marker in EMPTY_PROJECT_MARKERS)


def _active_project_count_line(total_count: int) -> str:
    return (
        f"当前 active 项目有 {total_count} 个。每日目标生成时，每个 active 项目都要生成一个符合用户习惯的"
        "今日目标；不要在多个项目之间挑选单一主目标。"
    )


def _render_frontend_project_line(index: int, project: dict[str, Any]) -> str:
    name = _soul_line_value(project.get("name"), limit=120)
    summary = _soul_line_value(project.get("status_summary"), limit=180)
    target = _soul_line_value(repo.project_target_goal(project), limit=160)
    today_goal = _soul_line_value(repo.project_today_goal(project), limit=160)
    parts = [name]
    if summary:
        parts.append(f"当前进度：{summary}")
    if target:
        parts.append(f"项目最终目标：{target}")
    if today_goal:
        parts.append(f"项目今日目标：{today_goal}")
    parts.append("状态：active，保留未完成承接")
    return f"{index}. {'；'.join(parts)}。"


def _soul_line_value(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[。；;]+", "，", text).strip(" ，,")
    return text[:limit].strip()


def _backup_soul(soul_path: Path) -> Path:
    PROJECT_IMPORT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = PROJECT_IMPORT_BACKUP_DIR / f"SOUL_{stamp}.md"
    shutil.copy2(soul_path, backup_path)
    return backup_path


def _write_current_projects_section(soul_path: Path, rendered_section: str) -> None:
    text = soul_path.read_text(encoding="utf-8")
    start = text.find(SECTION_TITLE)
    if start < 0:
        raise SoulProjectImportError("SOUL.md 缺少 ## 当前项目 段落。")
    next_match = NEXT_SECTION_PATTERN.search(text, start + len(SECTION_TITLE))
    end = next_match.start() if next_match else len(text)
    soul_path.write_text(text[:start] + rendered_section + "\n\n" + text[end:].lstrip("\n"), encoding="utf-8")


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
    if entry.today_goal and entry.today_goal != repo.project_today_goal(project):
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
        "today_goal": entry.today_goal,
        "project_state_patch": _project_state_patch(entry),
        "completion_summary": "",
        "today_goal_policy": "create",
        "confidence": 1.0,
        "reason": "SOUL.md 当前项目列表新增项目。",
    }


def _update_item(entry: SoulProjectEntry, project: dict[str, Any], *, renamed: bool) -> dict[str, Any]:
    target_goal = entry.target_goal or repo.project_target_goal(project)
    today_goal = entry.today_goal or repo.project_today_goal(project)
    planning_bias = entry.planning_bias or str(project.get("planning_bias") or "")
    return {
        "action": "update_project",
        "project_id": int(project["id"]),
        "project_name": entry.name,
        "priority": entry.priority or project.get("priority") or "P2",
        "status_summary": entry.status_summary or str(project.get("status_summary") or ""),
        "planning_bias": planning_bias,
        "target_goal": target_goal,
        "today_goal": today_goal,
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
        "today_goal": repo.project_today_goal(project),
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
        "today_goal": entry.today_goal or (repo.project_today_goal(fallback_project) if fallback_project else ""),
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


def _result_message(
    counts: dict[str, int],
    lifecycle_payload: dict[str, Any],
) -> str:
    if lifecycle_payload.get("status") == "failed":
        return lifecycle_payload.get("message") or "SOUL.md 项目同步失败。"
    changed = counts["create_project"] + counts["update_project"] + counts["rename_project"] + counts["complete_project"]
    if not changed:
        return "SOUL.md 当前项目没有变化。"
    message = (
        "SOUL.md 当前项目已同步："
        f"新增 {counts['create_project']}，"
        f"更新 {counts['update_project']}，"
        f"改名 {counts['rename_project']}，"
        f"完成 {counts['complete_project']}。"
    )
    return message


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
