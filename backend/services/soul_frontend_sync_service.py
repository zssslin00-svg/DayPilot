from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import default_backup_dir
from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH


CURRENT_PROJECTS_TITLE = "## 当前项目"
RECENT_RECORDS_TITLE = "## 最近记录"
USER_PREFERENCES_TITLE = "## 用户偏好"
NEXT_SECTION_PATTERN = re.compile(r"^##\s+", flags=re.MULTILINE)
PROJECT_LINE_PATTERN = re.compile(r"^(\s*(?:[-*+]|(?:\d+)[.)、])\s*)(.+?)\s*$")
FIELD_LABELS = ("当前进度", "项目最终目标", "项目今日目标", "规划倾向", "状态")
RECENT_RECORD_LIMIT = 20
RECENT_RECORD_WINDOW_DAYS = 14
DETERMINISTIC_SUMMARY_LIMIT = 180
LLM_SUMMARY_TRIGGER_CHARS = 260
SOUL_FRONTEND_BACKUP_DIR = default_backup_dir()


def sync_checkin_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    checkin_id: int | str | None = None,
    *,
    soul_path: str | Path = SOUL_PATH,
    settings: DayPilotSettings | None = None,
) -> dict[str, Any]:
    if checkin_id is None:
        return _not_applicable("missing_checkin_id")
    connection = initialize_database(db_path)
    try:
        checkin = repo.get_daily_checkin(connection, int(checkin_id))
        if checkin is None:
            return _not_applicable("checkin_not_found")
        daily_goal = repo.get_daily_goal(connection, int(checkin["daily_goal_id"]))
        if daily_goal is None:
            return _not_applicable("daily_goal_not_found")
        project = repo.get_project(connection, int(daily_goal["project_id"]))
        active_version = (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal.get("active_version_id") is not None
            else None
        )
    finally:
        connection.close()

    project_name = str((project or {}).get("name") or "").strip()
    completion = _clean_text(checkin.get("completion_text"))
    tomorrow = _clean_text(checkin.get("tomorrow_direction"))
    goal_text = _clean_text((active_version or {}).get("main_goal"))
    status_text = "完成" if str(checkin.get("completion_status") or "completed") == "completed" else "未完成"
    summary = _join_parts(
        [
            f"{status_text}：{completion}" if completion else status_text,
            f"目标：{goal_text}" if goal_text and not completion else "",
            f"下一步：{tomorrow}" if tomorrow else "",
        ]
    )
    summarized = _summarize_if_needed(summary, settings=settings, soul_path=Path(soul_path))
    record_date = _safe_date(str(checkin.get("checkin_date") or "")) or date.today()
    return _sync_frontend_activity_to_soul(
        soul_path=Path(soul_path),
        project_name=project_name,
        progress=f"{record_date.isoformat()} check-in：{summarized['text']}",
        today_goal=None,
        record_date=record_date,
        record_type="check-in",
        record_summary=f"{project_name or '未匹配项目'}：{summarized['text']}",
        summary_metadata=summarized,
    )


def sync_goal_feedback_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    daily_goal_id: int,
    feedback_message_id: int | None,
    revised_goal: dict[str, Any] | None,
    soul_path: str | Path = SOUL_PATH,
    settings: DayPilotSettings | None = None,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        daily_goal = repo.get_daily_goal(connection, int(daily_goal_id))
        if daily_goal is None:
            return _not_applicable("daily_goal_not_found")
        project = repo.get_project(connection, int(daily_goal["project_id"]))
        feedback = repo.get_feedback_message(connection, int(feedback_message_id)) if feedback_message_id else None
        active_version = (
            repo.get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal.get("active_version_id") is not None
            else None
        )
    finally:
        connection.close()

    project_name = str((project or {}).get("name") or "").strip()
    main_goal = _clean_text((revised_goal or {}).get("main_goal") or (active_version or {}).get("main_goal"))
    feedback_text = _clean_text((feedback or {}).get("raw_message"))
    summary = _join_parts(
        [
            f"反馈：{feedback_text}" if feedback_text else "目标已根据前端反馈修正",
            f"新目标：{main_goal}" if main_goal else "",
        ]
    )
    summarized = _summarize_if_needed(summary, settings=settings, soul_path=Path(soul_path))
    record_date = _safe_date(str(daily_goal.get("goal_date") or "")) or date.today()
    return _sync_frontend_activity_to_soul(
        soul_path=Path(soul_path),
        project_name=project_name,
        progress=None,
        today_goal=main_goal or None,
        record_date=record_date,
        record_type="goal-feedback",
        record_summary=f"{project_name or '未匹配项目'}：{summarized['text']}",
        summary_metadata=summarized,
    )


def sync_weekly_report_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    weekly_report_id: int,
    action: str,
    feedback_message: str | None = None,
    soul_path: str | Path = SOUL_PATH,
    settings: DayPilotSettings | None = None,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        weekly_report = repo.get_weekly_report(connection, int(weekly_report_id))
    finally:
        connection.close()
    if weekly_report is None:
        return _not_applicable("weekly_report_not_found")

    completed = _bullets_from_text(weekly_report.get("completed_work"))[:2]
    next_plan = _bullets_from_text(weekly_report.get("next_week_plan"))[:2]
    summary = _join_parts(
        [
            f"{'修正' if action == 'feedback' else '生成'}周报 {weekly_report.get('week_id')}",
            f"完成：{'；'.join(completed)}" if completed else "",
            f"下周：{'；'.join(next_plan)}" if next_plan else "",
            f"反馈：{_clean_text(feedback_message)}" if feedback_message else "",
        ]
    )
    summarized = _summarize_if_needed(summary, settings=settings, soul_path=Path(soul_path))
    record_date = _safe_date(str(weekly_report.get("generated_on_date") or "")) or date.today()
    return _sync_frontend_activity_to_soul(
        soul_path=Path(soul_path),
        project_name=None,
        progress=None,
        today_goal=None,
        record_date=record_date,
        record_type="weekly-report",
        record_summary=summarized["text"],
        summary_metadata=summarized,
    )


def sync_career_recommendation_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    action_id: int,
    allow_current_project_append: bool,
    today: date,
    soul_path: str | Path = SOUL_PATH,
    settings: DayPilotSettings | None = None,
) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        action = repo.get_career_recommendation_action(connection, int(action_id))
        if action is None:
            return _not_applicable("career_recommendation_action_not_found")
        project = repo.get_project(connection, int(action["project_id"]))
    finally:
        connection.close()
    if project is None:
        return _not_applicable("project_not_found")

    recommendation = action.get("recommendation_snapshot") or {}
    title = _clean_text(recommendation.get("title"))
    deliverable = _clean_text(recommendation.get("deliverable"))
    summary = _join_parts(
        [
            f"采纳职业建议：{title}" if title else "采纳职业建议",
            f"交付物：{deliverable}" if deliverable else "",
            f"绑定项目：{project.get('name')}",
        ]
    )
    summarized = _summarize_if_needed(summary, settings=settings, soul_path=Path(soul_path))
    append_result: dict[str, Any] = {"status": "not_applicable"}
    if allow_current_project_append:
        append_result = append_current_project_to_soul(
            soul_path=Path(soul_path),
            project_name=str(project.get("name") or ""),
            progress=_clean_text(project.get("status_summary")) or summarized["text"],
            target_goal=_clean_text(repo.project_target_goal(project)) or deliverable,
            today_goal="",
        )
        if append_result.get("status") in {"synced", "no_change"}:
            try:
                from backend.services.soul_project_import_service import import_current_projects_from_soul

                import_payload = import_current_projects_from_soul(
                    db_path,
                    soul_path=soul_path,
                    today=today,
                ).payload
            except Exception as exc:  # noqa: BLE001 - adoption is already saved
                import_payload = {"status": "failed", "reason": _safe_error(exc)}
            append_result["import"] = import_payload

    activity_result = _sync_frontend_activity_to_soul(
        soul_path=Path(soul_path),
        project_name=None,
        progress=None,
        today_goal=None,
        record_date=today,
        record_type="career-recommendation",
        record_summary=summarized["text"],
        summary_metadata=summarized,
    )
    return {
        "status": _combine_status(append_result, activity_result),
        "soul_synced": bool(append_result.get("soul_synced") or activity_result.get("soul_synced")),
        "soul_backup": activity_result.get("soul_backup") or append_result.get("soul_backup"),
        "current_project_append": append_result,
        "activity": activity_result,
        "summary_method": summarized["method"],
    }


def record_frontend_activity_to_soul(
    *,
    soul_path: str | Path = SOUL_PATH,
    record_date: date,
    record_type: str,
    summary: str,
    settings: DayPilotSettings | None = None,
) -> dict[str, Any]:
    summarized = _summarize_if_needed(summary, settings=settings, soul_path=Path(soul_path))
    return _sync_frontend_activity_to_soul(
        soul_path=Path(soul_path),
        project_name=None,
        progress=None,
        today_goal=None,
        record_date=record_date,
        record_type=record_type,
        record_summary=summarized["text"],
        summary_metadata=summarized,
    )


def append_current_project_to_soul(
    *,
    soul_path: Path,
    project_name: str,
    progress: str = "",
    target_goal: str = "",
    today_goal: str = "",
) -> dict[str, Any]:
    project_name = _clean_text(project_name)
    if not project_name:
        return _not_applicable("missing_project_name")
    try:
        text = soul_path.read_text(encoding="utf-8")
        start, end = _section_bounds(text, CURRENT_PROJECTS_TITLE)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    section = text[start:end].rstrip()
    lines = section.splitlines()
    for line in lines:
        if _project_line_mentions(line, project_name):
            return {
                "status": "no_change",
                "reason": "project_already_in_soul",
                "soul_synced": False,
                "soul_sync_queued": False,
                "soul_sync_retry_job_id": None,
            }

    insert_index = _current_project_insert_index(lines)
    next_lines = [line for line in lines if not _is_empty_project_marker(line)]
    if insert_index > len(next_lines):
        insert_index = len(next_lines)
    next_index = _next_project_index(next_lines[:insert_index])
    new_line = _render_project_line(
        next_index,
        project_name=project_name,
        progress=progress,
        target_goal=target_goal,
        today_goal=today_goal,
    )
    if insert_index > 0 and next_lines[insert_index - 1].strip():
        next_lines.insert(insert_index, "")
        insert_index += 1
    next_lines.insert(insert_index, new_line)
    next_lines = _update_active_project_count(next_lines, next_index)
    next_section = "\n".join(next_lines).rstrip()
    next_text = text[:start] + next_section + "\n\n" + text[end:].lstrip("\n")
    if next_text == text:
        return {"status": "no_change", "soul_synced": False}
    try:
        backup_path = _backup_soul(soul_path)
        soul_path.write_text(next_text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)
    return {
        "status": "synced",
        "soul_synced": True,
        "soul_backup": str(backup_path),
        "soul_sync_queued": False,
        "soul_sync_retry_job_id": None,
        "project_name": project_name,
    }


def _sync_frontend_activity_to_soul(
    *,
    soul_path: Path,
    project_name: str | None,
    progress: str | None,
    today_goal: str | None,
    record_date: date,
    record_type: str,
    record_summary: str,
    summary_metadata: dict[str, Any],
) -> dict[str, Any]:
    try:
        original = soul_path.read_text(encoding="utf-8")
        next_text, project_update = _update_current_project_fields(
            original,
            project_name=project_name,
            progress=progress,
            today_goal=today_goal,
        )
        next_text = _upsert_recent_record(
            next_text,
            record_date=record_date,
            record_type=record_type,
            record_summary=record_summary,
        )
        if next_text == original:
            return {
                "status": "no_change",
                "soul_synced": False,
                "soul_sync_queued": False,
                "soul_sync_retry_job_id": None,
                "project_update": project_update,
                "summary_method": summary_metadata["method"],
            }
        backup_path = _backup_soul(soul_path)
        soul_path.write_text(next_text, encoding="utf-8")
        return {
            "status": "synced" if project_update.get("status") != "project_not_found" else "partial",
            "soul_synced": True,
            "soul_backup": str(backup_path),
            "soul_sync_queued": False,
            "soul_sync_retry_job_id": None,
            "project_update": project_update,
            "recent_record": "added",
            "summary_method": summary_metadata["method"],
        }
    except Exception as exc:  # noqa: BLE001 - frontend state is already persisted
        return _failed(exc)


def _update_current_project_fields(
    text: str,
    *,
    project_name: str | None,
    progress: str | None,
    today_goal: str | None,
) -> tuple[str, dict[str, Any]]:
    if not project_name or (not progress and not today_goal):
        return text, {"status": "not_applicable"}
    try:
        start, end = _section_bounds(text, CURRENT_PROJECTS_TITLE)
    except ValueError:
        return text, {"status": "project_section_not_found"}
    section = text[start:end]
    lines = section.splitlines()
    changed = False
    found = False
    next_lines: list[str] = []
    for line in lines:
        if _project_line_mentions(line, project_name):
            found = True
            next_line = line
            if progress:
                next_line = _replace_or_append_field(next_line, "当前进度", progress)
            if today_goal:
                next_line = _replace_or_append_field(next_line, "项目今日目标", today_goal)
            changed = changed or next_line != line
            next_lines.append(next_line)
        else:
            next_lines.append(line)
    if not found:
        return text, {"status": "project_not_found", "project_name": project_name}
    if not changed:
        return text, {"status": "no_change", "project_name": project_name}
    next_section = "\n".join(next_lines)
    return text[:start] + next_section + text[end:], {"status": "updated", "project_name": project_name}


def _replace_or_append_field(line: str, label: str, value: str) -> str:
    clean_value = _soul_line_value(value, limit=180 if label == "当前进度" else 160)
    if not clean_value:
        return line
    next_label = "|".join(re.escape(item) for item in FIELD_LABELS if item != label)
    pattern = re.compile(
        rf"({re.escape(label)}\s*[：:])\s*.*?(?=(?:[；;]\s*(?:{next_label})\s*[：:])|[。.]?\s*$)"
    )
    if pattern.search(line):
        return pattern.sub(lambda match: f"{match.group(1)}{clean_value}", line, count=1)
    ending = ""
    body = line.rstrip()
    if body.endswith(("。", ".")):
        ending = body[-1]
        body = body[:-1].rstrip()
    separator = "；" if "：" in body or ":" in body else "："
    if separator == "：":
        return f"{body}：{label}：{clean_value}{ending or '。'}"
    return f"{body}；{label}：{clean_value}{ending or '。'}"


def _upsert_recent_record(
    text: str,
    *,
    record_date: date,
    record_type: str,
    record_summary: str,
) -> str:
    summary = _soul_line_value(record_summary, limit=220)
    if not summary:
        return text
    line = f"- {record_date.isoformat()} [{record_type}] {summary}"
    try:
        start, end = _section_bounds(text, RECENT_RECORDS_TITLE)
        existing_section = text[start:end]
        existing_lines = _recent_record_lines(existing_section)
        next_lines = [RECENT_RECORDS_TITLE, "", line]
        next_lines.extend(item for item in existing_lines if item != line)
        next_section = "\n".join(_trim_recent_record_lines(next_lines, today=record_date)).rstrip()
        return text[:start] + next_section + "\n\n" + text[end:].lstrip("\n")
    except ValueError:
        insert_at = _recent_records_insert_position(text)
        rendered = "\n".join([RECENT_RECORDS_TITLE, "", line]).rstrip()
        prefix = text[:insert_at].rstrip()
        suffix = text[insert_at:].lstrip("\n")
        return f"{prefix}\n\n{rendered}\n\n{suffix}"


def _recent_record_lines(section: str) -> list[str]:
    return [line.strip() for line in section.splitlines() if line.strip().startswith("- ")]


def _trim_recent_record_lines(lines: list[str], *, today: date) -> list[str]:
    header = lines[:2] if len(lines) >= 2 else [RECENT_RECORDS_TITLE, ""]
    records = [line for line in lines[2:] if line.strip().startswith("- ")]
    cutoff = today - timedelta(days=RECENT_RECORD_WINDOW_DAYS)
    retained: list[str] = []
    for line in records:
        item_date = _safe_date(line.strip()[2:12])
        if item_date is not None and item_date < cutoff:
            continue
        retained.append(line)
    return header + retained[:RECENT_RECORD_LIMIT]


def _recent_records_insert_position(text: str) -> int:
    try:
        _start, end = _section_bounds(text, CURRENT_PROJECTS_TITLE)
        return end
    except ValueError:
        next_match = NEXT_SECTION_PATTERN.search(text)
        return next_match.start() if next_match else len(text)


def _section_bounds(text: str, title: str) -> tuple[int, int]:
    start = text.find(title)
    if start < 0:
        raise ValueError(f"SOUL.md missing section: {title}")
    next_match = NEXT_SECTION_PATTERN.search(text, start + len(title))
    end = next_match.start() if next_match else len(text)
    return start, end


def _project_line_mentions(line: str, project_name: str) -> bool:
    match = PROJECT_LINE_PATTERN.match(line)
    if match is None:
        return False
    content = match.group(2)
    first_field = re.split(r"[：:；;。]", content, maxsplit=1)[0].strip()
    return _name_key(project_name) == _name_key(first_field) or _name_key(project_name) in _name_key(content)


def _next_project_index(lines: list[str]) -> int:
    count = sum(1 for line in lines if PROJECT_LINE_PATTERN.match(line) and not _is_empty_project_marker(line))
    return count + 1


def _current_project_insert_index(lines: list[str]) -> int:
    stop_markers = ("本段落", "每日生成规则")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(marker) for marker in stop_markers):
            return index
    return len(lines)


def _render_project_line(
    index: int,
    *,
    project_name: str,
    progress: str,
    target_goal: str,
    today_goal: str,
) -> str:
    parts = [_soul_line_value(project_name, limit=120)]
    if progress:
        parts.append(f"当前进度：{_soul_line_value(progress, limit=180)}")
    if target_goal:
        parts.append(f"项目最终目标：{_soul_line_value(target_goal, limit=160)}")
    if today_goal:
        parts.append(f"项目今日目标：{_soul_line_value(today_goal, limit=160)}")
    return f"{index}. {'；'.join(parts)}。"


def _update_active_project_count(lines: list[str], total_count: int) -> list[str]:
    pattern = re.compile(r"(当前\s*active\s*项目(?:共有|有|数量)?\s*)\d+(\s*个)")
    for index, line in enumerate(lines):
        if pattern.search(line):
            lines[index] = pattern.sub(rf"\g<1>{total_count}\2", line, count=1)
            return lines
    if lines and lines[0].strip() == CURRENT_PROJECTS_TITLE:
        return lines[:1] + ["", f"当前 active 项目有 {total_count} 个。"] + lines[1:]
    return lines


def _is_empty_project_marker(line: str) -> bool:
    return "暂无 active 项目" in line or "暂无当前项目" in line


def _summarize_if_needed(
    text: str,
    *,
    settings: DayPilotSettings | None,
    soul_path: Path,
    max_chars: int = DETERMINISTIC_SUMMARY_LIMIT,
) -> dict[str, Any]:
    deterministic = _compact_text(text, max_chars=max_chars)
    if len(_clean_text(text)) <= LLM_SUMMARY_TRIGGER_CHARS:
        return {"text": deterministic, "method": "deterministic"}
    try:
        llm_result = generate_json_with_fallback(
            task_name="soul_frontend_activity_summary",
            prompt_version_deepseek="soul_frontend_activity_summary_v1_deepseek",
            prompt_version_mock="soul_frontend_activity_summary_v1_mock",
            mock_model_name="mock-soul-activity-summary",
            build_messages=lambda soul: _summary_messages(text, max_chars, soul),
            mock_generate=lambda: {"summary": deterministic},
            validator=_validate_summary_output,
            settings=settings,
            soul_path=soul_path,
        )
        summary = _compact_text(llm_result.output.get("summary"), max_chars=max_chars)
        return {
            "text": summary or deterministic,
            "method": llm_result.metadata.get("llm_mode_used") or "llm",
        }
    except Exception:  # noqa: BLE001 - SOUL sync must never block the frontend action
        return {"text": deterministic, "method": "truncated_after_llm_failure"}


def _summary_messages(text: str, max_chars: int, soul: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Summarize one DayPilot frontend activity for SOUL.md. "
                "Return only JSON with a concise Chinese summary."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "max_chars": max_chars,
                    "activity_text": text,
                    "soul_context_excerpt": soul[:1200],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _validate_summary_output(output: dict[str, Any]) -> None:
    if not isinstance(output, dict):
        raise ValueError("summary_output_not_object")
    if not _clean_text(output.get("summary")):
        raise ValueError("summary_missing")


def _backup_soul(soul_path: Path) -> Path:
    SOUL_FRONTEND_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = SOUL_FRONTEND_BACKUP_DIR / f"SOUL_{stamp}.md"
    shutil.copy2(soul_path, backup_path)
    return backup_path


def _bullets_from_text(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped:
            items.append(stripped)
    return items


def _join_parts(parts: list[str]) -> str:
    return "；".join(part for part in (_clean_text(item) for item in parts) if part)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact_text(value: Any, *, max_chars: int) -> str:
    text = _clean_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip("，,；;。 ") + "…"


def _soul_line_value(value: Any, *, limit: int) -> str:
    text = _compact_text(value, max_chars=limit)
    return re.sub(r"[。；;]+", "；", text).strip(" ；")


def _safe_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _name_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def _combine_status(*items: dict[str, Any]) -> str:
    statuses = {str(item.get("status") or "not_applicable") for item in items}
    if "failed" in statuses:
        return "partial" if any(status in {"synced", "no_change"} for status in statuses) else "failed"
    if "partial" in statuses:
        return "partial"
    if "synced" in statuses:
        return "synced"
    if "no_change" in statuses:
        return "no_change"
    return "not_applicable"


def _not_applicable(reason: str) -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "reason": reason,
        "soul_synced": False,
        "soul_backup": None,
    }


def _failed(exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": _safe_error(exc),
        "soul_synced": False,
        "soul_backup": None,
        "soul_sync_queued": False,
        "soul_sync_retry_job_id": None,
    }


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
