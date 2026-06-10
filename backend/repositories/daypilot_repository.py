from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from backend.services.workday_policy import is_workday


Record = dict[str, Any]

TABLE_COLUMNS: dict[str, set[str]] = {
    "user_profile": {
        "id",
        "display_name",
        "long_term_direction",
        "current_focus_projects",
        "goal_preferences",
        "avoid_patterns",
        "default_available_minutes",
        "timezone",
        "workday_rule",
        "created_at",
        "updated_at",
    },
    "projects": {
        "id",
        "name",
        "priority",
        "role",
        "status",
        "project_state",
        "created_at",
        "updated_at",
    },
    "daily_goals": {
        "id",
        "profile_id",
        "project_id",
        "goal_date",
        "week_id",
        "weekday",
        "is_workday",
        "status",
        "active_version_id",
        "context_snapshot",
        "revision_count",
        "generated_at",
        "checked_in_at",
        "created_at",
        "updated_at",
    },
    "goal_versions": {
        "id",
        "daily_goal_id",
        "version_no",
        "is_active",
        "main_goal",
        "goal_reason",
        "success_criteria",
        "estimated_minutes",
        "difficulty_level",
        "minimum_version",
        "stretch_challenge",
        "avoid_today",
        "goal_type",
        "revision_source",
        "revision_reason",
        "feedback_message_id",
        "critic_result",
        "prompt_version",
        "created_at",
    },
    "daily_checkins": {
        "id",
        "daily_goal_id",
        "checkin_date",
        "week_id",
        "is_workday",
        "completion_status",
        "completion_text",
        "felt_difficulty",
        "tomorrow_direction",
        "parsed_completion_rate",
        "completed_items",
        "unfinished_items",
        "blockers",
        "actual_outputs",
        "processor_snapshot",
        "created_at",
        "updated_at",
    },
    "project_progress_events": {
        "id",
        "project_id",
        "event_date",
        "source_type",
        "source_id",
        "event_status",
        "progress_delta",
        "evidence_text",
        "confidence",
        "applied_to_summary",
        "previous_status_summary",
        "new_status_summary",
        "reason",
        "llm_metadata",
        "raw_output",
        "created_at",
    },
    "project_lifecycle_events": {
        "id",
        "event_date",
        "raw_message",
        "action",
        "project_id",
        "project_name",
        "priority",
        "previous_status",
        "new_status",
        "previous_status_summary",
        "new_status_summary",
        "planning_bias",
        "confidence",
        "applied",
        "reason",
        "llm_metadata",
        "raw_output",
        "created_at",
    },
    "feedback_messages": {
        "id",
        "daily_goal_id",
        "before_version_id",
        "after_version_id",
        "raw_message",
        "feedback_type",
        "affected_scope",
        "interpretation_json",
        "extracted_constraints",
        "extracted_preferences",
        "memory_action",
        "should_regenerate_goal",
        "is_resolved",
        "created_at",
    },
    "profile_memory_events": {
        "id",
        "feedback_message_id",
        "daily_goal_id",
        "raw_feedback",
        "preference_items",
        "avoid_items",
        "time_scope_rules",
        "ignored_items",
        "previous_goal_preferences",
        "new_goal_preferences",
        "soul_backup_path",
        "confidence",
        "applied",
        "reason",
        "llm_metadata",
        "raw_output",
        "created_at",
    },
    "soul_sync_retry_jobs": {
        "id",
        "job_type",
        "status",
        "source_table",
        "source_id",
        "payload",
        "attempts",
        "last_error",
        "next_retry_at",
        "created_at",
        "updated_at",
    },
    "ability_state": {
        "id",
        "state_date",
        "source_checkin_id",
        "source_feedback_message_id",
        "current_difficulty",
        "target_difficulty_level",
        "recent_completion_rate",
        "recent_felt_difficulty_avg",
        "completion_streak",
        "low_completion_streak",
        "overload_count",
        "underload_count",
        "default_estimated_minutes",
        "preferred_goal_type_weights",
        "short_term_preferences",
        "long_term_preferences_snapshot",
        "avoid_patterns_snapshot",
        "adjustment_direction",
        "update_reason",
        "is_current",
        "created_at",
    },
    "weekly_reports": {
        "id",
        "week_id",
        "week_start_date",
        "week_end_date",
        "generated_on_date",
        "status",
        "completed_work",
        "next_week_plan",
        "weekly_reflection",
        "report_text",
        "source_snapshot",
        "next_week_focus_summary",
        "quality_score",
        "prompt_version",
        "model_name",
        "created_at",
        "updated_at",
    },
    "weekly_report_versions": {
        "id",
        "weekly_report_id",
        "week_id",
        "version_no",
        "revision_source",
        "revision_reason",
        "feedback_message",
        "completed_work",
        "next_week_plan",
        "weekly_reflection",
        "report_text",
        "source_snapshot",
        "llm_metadata",
        "created_at",
    },
    "weekly_focus": {
        "id",
        "weekly_report_id",
        "source_week_id",
        "target_week_id",
        "focus_order",
        "focus_text",
        "desired_outcome",
        "focus_type",
        "priority",
        "status",
        "context_payload",
        "carried_into_goal_id",
        "created_at",
        "updated_at",
    },
}

JSON_FIELDS: dict[str, set[str]] = {
    "user_profile": {
        "current_focus_projects",
        "goal_preferences",
        "avoid_patterns",
        "workday_rule",
    },
    "projects": {"project_state"},
    "daily_goals": {"context_snapshot"},
    "goal_versions": {"success_criteria", "critic_result"},
    "daily_checkins": {
        "completed_items",
        "unfinished_items",
        "blockers",
        "actual_outputs",
        "processor_snapshot",
    },
    "project_progress_events": {"llm_metadata", "raw_output"},
    "project_lifecycle_events": {"llm_metadata", "raw_output"},
    "feedback_messages": {
        "interpretation_json",
        "extracted_constraints",
        "extracted_preferences",
    },
    "profile_memory_events": {
        "preference_items",
        "avoid_items",
        "time_scope_rules",
        "ignored_items",
        "previous_goal_preferences",
        "new_goal_preferences",
        "llm_metadata",
        "raw_output",
    },
    "soul_sync_retry_jobs": {"payload"},
    "ability_state": {
        "preferred_goal_type_weights",
        "short_term_preferences",
        "long_term_preferences_snapshot",
        "avoid_patterns_snapshot",
    },
    "weekly_reports": {"source_snapshot"},
    "weekly_report_versions": {"source_snapshot", "llm_metadata"},
    "weekly_focus": {"context_payload"},
}

UPDATED_AT_TABLES = {
    "user_profile",
    "projects",
    "daily_goals",
    "daily_checkins",
    "soul_sync_retry_jobs",
    "weekly_reports",
    "weekly_focus",
}

PROJECT_STATE_SCHEMA_VERSION = "project_state.v1"
PROJECT_FACT_TYPES = {
    "progress",
    "decision",
    "constraint",
    "next_step",
    "artifact",
    "risk",
    "open_question",
    "context",
}


def normalize_project_state(value: Any, **overrides: Any) -> dict[str, Any]:
    """Return a canonical project_state object with stable keys."""

    state = _decode_json_mapping(value)
    summary = overrides.get("summary", state.get("summary"))
    planning_guidance = overrides.get(
        "planning_guidance",
        state.get("planning_guidance", state.get("planning_bias")),
    )
    target_goal = overrides.get("target_goal", state.get("target_goal"))
    facts = overrides.get("facts", state.get("facts"))
    updated_from = overrides.get("updated_from", state.get("updated_from"))
    return {
        "schema_version": str(state.get("schema_version") or PROJECT_STATE_SCHEMA_VERSION),
        "summary": str(summary or "").strip(),
        "planning_guidance": str(planning_guidance or "").strip(),
        "target_goal": str(target_goal or "").strip(),
        "facts": _normalize_project_facts(facts),
        "updated_from": updated_from if isinstance(updated_from, dict) else {},
    }


def project_state_from_legacy(
    *,
    status_summary: Any = "",
    planning_bias: Any = "",
    source_payload: Any = None,
    existing_state: Any = None,
    updated_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _decode_json_mapping(source_payload)
    state = normalize_project_state(existing_state)
    target_goal = payload.get("target_goal", state.get("target_goal"))
    state = normalize_project_state(
        state,
        summary=status_summary if status_summary is not None else state.get("summary"),
        planning_guidance=planning_bias if planning_bias is not None else state.get("planning_guidance"),
        target_goal=target_goal,
        updated_from=updated_from
        or {
            "source": "legacy_project_fields",
            "payload_keys": sorted(str(key) for key in payload.keys()),
        },
    )
    patch = payload.get("project_state_patch")
    if isinstance(patch, Mapping):
        state = merge_project_state(state, patch)
    return state


def merge_project_state(
    existing_state: Any,
    patch: Mapping[str, Any] | None,
    *,
    updated_from: dict[str, Any] | None = None,
    replace_source_facts: bool = False,
    source_type: str | None = None,
    source_id: int | str | None = None,
) -> dict[str, Any]:
    state = normalize_project_state(existing_state)
    patch_data = dict(patch or {})
    if replace_source_facts and source_type and source_id is not None:
        state["facts"] = [
            fact
            for fact in state["facts"]
            if not (
                str(fact.get("source_type") or "") == str(source_type)
                and str(fact.get("source_id") or "") == str(source_id)
            )
        ]
    for key in ("summary", "planning_guidance", "target_goal"):
        if key in patch_data:
            state[key] = str(patch_data.get(key) or "").strip()
    facts = _normalize_project_facts(patch_data.get("facts"))
    if source_type and source_id is not None:
        for fact in facts:
            fact.setdefault("source_type", source_type)
            fact.setdefault("source_id", source_id)
    if facts:
        state["facts"].extend(facts)
    patch_source = patch_data.get("updated_from")
    if updated_from is not None:
        state["updated_from"] = updated_from
    elif isinstance(patch_source, dict):
        state["updated_from"] = patch_source
    return normalize_project_state(state)


def project_status_summary(project: Mapping[str, Any] | None) -> str:
    if project is None:
        return ""
    return normalize_project_state(project.get("project_state")).get("summary", "")


def project_planning_bias(project: Mapping[str, Any] | None) -> str:
    if project is None:
        return ""
    return normalize_project_state(project.get("project_state")).get("planning_guidance", "")


def project_target_goal(project: Mapping[str, Any] | None) -> str:
    if project is None:
        return ""
    return normalize_project_state(project.get("project_state")).get("target_goal", "")


def project_state_hash(project_or_state: Mapping[str, Any] | None) -> str:
    value: Any
    if isinstance(project_or_state, Mapping) and "project_state" in project_or_state:
        value = project_or_state.get("project_state")
    else:
        value = project_or_state
    state = normalize_project_state(value)
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_user_profile(connection: sqlite3.Connection, **profile: Any) -> int:
    return _insert(connection, "user_profile", profile)


def get_user_profile(connection: sqlite3.Connection, profile_id: int = 1) -> Record | None:
    return _fetch_by_id(connection, "user_profile", profile_id)


def update_user_profile(connection: sqlite3.Connection, profile_id: int = 1, **changes: Any) -> Record | None:
    return _update(connection, "user_profile", profile_id, changes)


def create_project(connection: sqlite3.Connection, **project: Any) -> int:
    return _insert(connection, "projects", _prepare_project_record(project, for_insert=True))


def get_project(connection: sqlite3.Connection, project_id: int) -> Record | None:
    return _fetch_by_id(connection, "projects", project_id)


def get_project_by_name(connection: sqlite3.Connection, name: str) -> Record | None:
    return _fetch_one(
        connection,
        "projects",
        "SELECT * FROM projects WHERE name = ?",
        (name,),
    )


def update_project(connection: sqlite3.Connection, project_id: int, **changes: Any) -> Record | None:
    existing = get_project(connection, project_id)
    return _update(connection, "projects", project_id, _prepare_project_record(changes, existing=existing))


def delete_project(connection: sqlite3.Connection, project_id: int) -> Record | None:
    project = get_project(connection, project_id)
    if project is None:
        return None
    connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return project


def list_projects(connection: sqlite3.Connection, *, include_archived: bool = False) -> list[Record]:
    where = "" if include_archived else "WHERE status = 'active'"
    return _fetch_all(
        connection,
        "projects",
        f"""
        SELECT *
        FROM projects
        {where}
        ORDER BY id
        """,
        (),
    )


def list_completed_projects(connection: sqlite3.Connection, *, limit: int = 10) -> list[Record]:
    return _fetch_all(
        connection,
        "projects",
        """
        SELECT *
        FROM projects
        WHERE status = 'completed'
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def create_daily_goal(connection: sqlite3.Connection, **daily_goal: Any) -> int:
    goal = dict(daily_goal)
    if "goal_date" in goal:
        goal_date = date.fromisoformat(str(goal["goal_date"]))
        goal.setdefault("week_id", _week_id(goal_date))
        goal.setdefault("weekday", goal_date.isoweekday())
        goal.setdefault("is_workday", int(is_workday(goal_date)))
    if not goal.get("project_id"):
        goal["project_id"] = _ensure_default_project(connection)
    return _insert(connection, "daily_goals", goal)


def get_daily_goal(connection: sqlite3.Connection, daily_goal_id: int) -> Record | None:
    return _fetch_by_id(connection, "daily_goals", daily_goal_id)


def get_daily_goal_by_date(connection: sqlite3.Connection, goal_date: str) -> Record | None:
    return _fetch_one(
        connection,
        "daily_goals",
        "SELECT * FROM daily_goals WHERE goal_date = ? ORDER BY project_id, id",
        (goal_date,),
    )


def get_daily_goal_by_date_and_project(
    connection: sqlite3.Connection,
    goal_date: str,
    project_id: int,
) -> Record | None:
    return _fetch_one(
        connection,
        "daily_goals",
        "SELECT * FROM daily_goals WHERE goal_date = ? AND project_id = ?",
        (goal_date, project_id),
    )


def list_daily_goals_by_date(connection: sqlite3.Connection, goal_date: str) -> list[Record]:
    return _fetch_all(
        connection,
        "daily_goals",
        "SELECT * FROM daily_goals WHERE goal_date = ? ORDER BY project_id, id",
        (goal_date,),
    )


def update_daily_goal(connection: sqlite3.Connection, daily_goal_id: int, **changes: Any) -> Record | None:
    return _update(connection, "daily_goals", daily_goal_id, changes)


def list_daily_goals_by_week(
    connection: sqlite3.Connection,
    week_id: str,
    *,
    monday_to_friday_only: bool = True,
) -> list[Record]:
    where = "week_id = ?"
    params: list[Any] = [week_id]
    if monday_to_friday_only:
        where += " AND is_workday = 1"
    return _fetch_all(
        connection,
        "daily_goals",
        f"SELECT * FROM daily_goals WHERE {where} ORDER BY weekday, goal_date, project_id, id",
        params,
    )


def list_recent_daily_goal_records(
    connection: sqlite3.Connection,
    before_date: str,
    *,
    limit: int = 7,
) -> list[Record]:
    daily_goals = _fetch_all(
        connection,
        "daily_goals",
        """
        SELECT *
        FROM daily_goals
        WHERE goal_date < ?
        ORDER BY goal_date DESC, project_id, id
        LIMIT ?
        """,
        (before_date, limit),
    )
    records: list[Record] = []
    for daily_goal in daily_goals:
        active_version = None
        if daily_goal["active_version_id"] is not None:
            active_version = get_goal_version(connection, int(daily_goal["active_version_id"]))
        records.append({"daily_goal": daily_goal, "active_version": active_version})
    return records


def list_recent_daily_goal_records_for_project(
    connection: sqlite3.Connection,
    before_date: str,
    project_id: int,
    *,
    limit: int = 7,
) -> list[Record]:
    daily_goals = _fetch_all(
        connection,
        "daily_goals",
        """
        SELECT *
        FROM daily_goals
        WHERE goal_date < ? AND project_id = ?
        ORDER BY goal_date DESC, id DESC
        LIMIT ?
        """,
        (before_date, project_id, limit),
    )
    records: list[Record] = []
    for daily_goal in daily_goals:
        active_version = None
        if daily_goal["active_version_id"] is not None:
            active_version = get_goal_version(connection, int(daily_goal["active_version_id"]))
        checkin = _fetch_one(
            connection,
            "daily_checkins",
            "SELECT * FROM daily_checkins WHERE daily_goal_id = ?",
            (int(daily_goal["id"]),),
        )
        records.append({"daily_goal": daily_goal, "active_version": active_version, "daily_checkin": checkin})
    return records


def list_daily_goal_records_between(
    connection: sqlite3.Connection,
    start_date: str,
    end_date: str,
) -> list[Record]:
    daily_goals = _fetch_all(
        connection,
        "daily_goals",
        """
        SELECT *
        FROM daily_goals
        WHERE goal_date BETWEEN ? AND ?
        ORDER BY goal_date DESC, project_id, id
        """,
        (start_date, end_date),
    )
    records: list[Record] = []
    for daily_goal in daily_goals:
        daily_goal_id = int(daily_goal["id"])
        active_version = (
            get_goal_version(connection, int(daily_goal["active_version_id"]))
            if daily_goal["active_version_id"] is not None
            else None
        )
        checkin = _fetch_one(
            connection,
            "daily_checkins",
            "SELECT * FROM daily_checkins WHERE daily_goal_id = ?",
            (daily_goal_id,),
        )
        records.append(
            {
                "daily_goal": daily_goal,
                "project": get_project(connection, int(daily_goal["project_id"])),
                "active_version": active_version,
                "goal_versions": list_goal_versions(connection, daily_goal_id),
                "daily_checkin": checkin,
                "feedback_messages": list_feedback_messages_for_goal(connection, daily_goal_id),
            }
        )
    return records


def create_goal_version(connection: sqlite3.Connection, **goal_version: Any) -> int:
    version = dict(goal_version)
    if int(version.get("is_active", 0)) == 1:
        connection.execute(
            "UPDATE goal_versions SET is_active = 0 WHERE daily_goal_id = ? AND is_active = 1",
            (version["daily_goal_id"],),
        )
    version_id = _insert(connection, "goal_versions", version)
    if int(version.get("is_active", 0)) == 1:
        update_daily_goal(connection, int(version["daily_goal_id"]), active_version_id=version_id)
    return version_id


def get_goal_version(connection: sqlite3.Connection, goal_version_id: int) -> Record | None:
    return _fetch_by_id(connection, "goal_versions", goal_version_id)


def list_goal_versions(connection: sqlite3.Connection, daily_goal_id: int) -> list[Record]:
    return _fetch_all(
        connection,
        "goal_versions",
        "SELECT * FROM goal_versions WHERE daily_goal_id = ? ORDER BY version_no",
        (daily_goal_id,),
    )


def create_daily_checkin(connection: sqlite3.Connection, **checkin: Any) -> int:
    checkin = dict(checkin)
    checkin.setdefault("completion_status", "completed")
    checkin_id = _insert(connection, "daily_checkins", checkin)
    update_daily_goal(
        connection,
        int(checkin["daily_goal_id"]),
        status="checked_in",
        checked_in_at=checkin.get("created_at") or _now_text(),
    )
    return checkin_id


def update_daily_checkin(connection: sqlite3.Connection, checkin_id: int, **changes: Any) -> Record | None:
    updated = _update(connection, "daily_checkins", checkin_id, changes)
    if updated is not None:
        update_daily_goal(
            connection,
            int(updated["daily_goal_id"]),
            status="checked_in",
            checked_in_at=updated.get("updated_at") or _now_text(),
        )
    return updated


def get_daily_checkin(connection: sqlite3.Connection, checkin_id: int) -> Record | None:
    return _fetch_by_id(connection, "daily_checkins", checkin_id)


def get_daily_checkin_by_date(connection: sqlite3.Connection, checkin_date: str) -> Record | None:
    return _fetch_one(
        connection,
        "daily_checkins",
        "SELECT * FROM daily_checkins WHERE checkin_date = ? ORDER BY daily_goal_id, id",
        (checkin_date,),
    )


def list_daily_checkins_by_date(connection: sqlite3.Connection, checkin_date: str) -> list[Record]:
    return _fetch_all(
        connection,
        "daily_checkins",
        "SELECT * FROM daily_checkins WHERE checkin_date = ? ORDER BY daily_goal_id, id",
        (checkin_date,),
    )


def create_project_progress_event(connection: sqlite3.Connection, **event: Any) -> int:
    return _insert(connection, "project_progress_events", event)


def get_project_progress_event(connection: sqlite3.Connection, event_id: int) -> Record | None:
    return _fetch_by_id(connection, "project_progress_events", event_id)


def list_project_progress_events_for_source(
    connection: sqlite3.Connection,
    source_type: str,
    source_id: int,
    *,
    active_only: bool = False,
) -> list[Record]:
    where = "source_type = ? AND source_id = ?"
    params: list[Any] = [source_type, source_id]
    if active_only:
        where += " AND event_status = 'active'"
    return _fetch_all(
        connection,
        "project_progress_events",
        f"""
        SELECT *
        FROM project_progress_events
        WHERE {where}
        ORDER BY created_at, id
        """,
        params,
    )


def list_recent_project_progress_events(
    connection: sqlite3.Connection,
    *,
    project_id: int | None = None,
    limit: int = 10,
) -> list[Record]:
    where = "event_status = 'active'"
    params: list[Any] = []
    if project_id is not None:
        where += " AND project_id = ?"
        params.append(project_id)
    params.append(limit)
    return _fetch_all(
        connection,
        "project_progress_events",
        f"""
        SELECT *
        FROM project_progress_events
        WHERE {where}
        ORDER BY event_date DESC, created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )


def supersede_project_progress_events_for_source(
    connection: sqlite3.Connection,
    source_type: str,
    source_id: int,
) -> None:
    connection.execute(
        """
        UPDATE project_progress_events
        SET event_status = 'superseded'
        WHERE source_type = ? AND source_id = ? AND event_status = 'active'
        """,
        (source_type, source_id),
    )


def create_project_lifecycle_event(connection: sqlite3.Connection, **event: Any) -> int:
    return _insert(connection, "project_lifecycle_events", event)


def get_project_lifecycle_event(connection: sqlite3.Connection, event_id: int) -> Record | None:
    return _fetch_by_id(connection, "project_lifecycle_events", event_id)


def list_recent_project_lifecycle_events(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[Record]:
    return _fetch_all(
        connection,
        "project_lifecycle_events",
        """
        SELECT *
        FROM project_lifecycle_events
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def list_daily_checkins_by_week(connection: sqlite3.Connection, week_id: str) -> list[Record]:
    return _fetch_all(
        connection,
        "daily_checkins",
        "SELECT * FROM daily_checkins WHERE week_id = ? ORDER BY checkin_date, daily_goal_id, id",
        (week_id,),
    )


def list_recent_daily_checkins(
    connection: sqlite3.Connection,
    before_date: str,
    *,
    limit: int = 7,
) -> list[Record]:
    return _fetch_all(
        connection,
        "daily_checkins",
        """
        SELECT *
        FROM daily_checkins
        WHERE checkin_date < ?
        ORDER BY checkin_date DESC, daily_goal_id, id
        LIMIT ?
        """,
        (before_date, limit),
    )


def list_latest_daily_checkins_through(
    connection: sqlite3.Connection,
    through_date: str,
    *,
    limit: int = 7,
) -> list[Record]:
    return _fetch_all(
        connection,
        "daily_checkins",
        """
        SELECT *
        FROM daily_checkins
        WHERE checkin_date <= ?
        ORDER BY checkin_date DESC, daily_goal_id, id
        LIMIT ?
        """,
        (through_date, limit),
    )


def create_feedback_message(connection: sqlite3.Connection, **feedback: Any) -> int:
    return _insert(connection, "feedback_messages", feedback)


def get_feedback_message(connection: sqlite3.Connection, feedback_message_id: int) -> Record | None:
    return _fetch_by_id(connection, "feedback_messages", feedback_message_id)


def update_feedback_message(
    connection: sqlite3.Connection,
    feedback_message_id: int,
    **changes: Any,
) -> Record | None:
    return _update(connection, "feedback_messages", feedback_message_id, changes)


def list_feedback_messages_for_goal(connection: sqlite3.Connection, daily_goal_id: int) -> list[Record]:
    return _fetch_all(
        connection,
        "feedback_messages",
        "SELECT * FROM feedback_messages WHERE daily_goal_id = ? ORDER BY created_at, id",
        (daily_goal_id,),
    )


def list_feedback_messages_by_date(connection: sqlite3.Connection, goal_date: str) -> list[Record]:
    return _fetch_all(
        connection,
        "feedback_messages",
        """
        SELECT feedback_messages.*
        FROM feedback_messages
        JOIN daily_goals ON daily_goals.id = feedback_messages.daily_goal_id
        WHERE daily_goals.goal_date = ?
        ORDER BY feedback_messages.created_at, feedback_messages.id
        """,
        (goal_date,),
    )


def list_recent_feedback_messages(
    connection: sqlite3.Connection,
    before_date: str,
    *,
    limit: int = 20,
) -> list[Record]:
    return _fetch_all(
        connection,
        "feedback_messages",
        """
        SELECT feedback_messages.*
        FROM feedback_messages
        JOIN daily_goals ON daily_goals.id = feedback_messages.daily_goal_id
        WHERE daily_goals.goal_date < ?
        ORDER BY feedback_messages.created_at DESC, feedback_messages.id DESC
        LIMIT ?
        """,
        (before_date, limit),
    )


def create_profile_memory_event(connection: sqlite3.Connection, **event: Any) -> int:
    return _insert(connection, "profile_memory_events", event)


def get_profile_memory_event(connection: sqlite3.Connection, event_id: int) -> Record | None:
    return _fetch_by_id(connection, "profile_memory_events", event_id)


def update_profile_memory_event(connection: sqlite3.Connection, event_id: int, **changes: Any) -> Record | None:
    return _update(connection, "profile_memory_events", event_id, changes)


def list_recent_profile_memory_events(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[Record]:
    return _fetch_all(
        connection,
        "profile_memory_events",
        """
        SELECT *
        FROM profile_memory_events
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def create_soul_sync_retry_job(connection: sqlite3.Connection, **job: Any) -> int:
    return _insert(connection, "soul_sync_retry_jobs", job)


def get_soul_sync_retry_job(connection: sqlite3.Connection, job_id: int) -> Record | None:
    return _fetch_by_id(connection, "soul_sync_retry_jobs", job_id)


def update_soul_sync_retry_job(connection: sqlite3.Connection, job_id: int, **changes: Any) -> Record | None:
    return _update(connection, "soul_sync_retry_jobs", job_id, changes)


def list_soul_sync_retry_jobs(
    connection: sqlite3.Connection,
    *,
    statuses: list[str] | tuple[str, ...] = ("pending", "failed", "retrying"),
    limit: int = 20,
) -> list[Record]:
    if not statuses:
        return []
    placeholders = ", ".join("?" for _ in statuses)
    params: list[Any] = list(statuses)
    params.append(limit)
    return _fetch_all(
        connection,
        "soul_sync_retry_jobs",
        f"""
        SELECT *
        FROM soul_sync_retry_jobs
        WHERE status IN ({placeholders})
        ORDER BY created_at, id
        LIMIT ?
        """,
        params,
    )


def soul_sync_retry_status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM soul_sync_retry_jobs
        GROUP BY status
        """
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def list_recent_soul_sync_retry_jobs(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[Record]:
    return _fetch_all(
        connection,
        "soul_sync_retry_jobs",
        """
        SELECT *
        FROM soul_sync_retry_jobs
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def create_ability_state(connection: sqlite3.Connection, **ability_state: Any) -> int:
    state = dict(ability_state)
    if int(state.get("is_current", 1)) == 1:
        clear_current_ability_state(connection)
    return _insert(connection, "ability_state", state)


def update_ability_state(connection: sqlite3.Connection, ability_state_id: int, **changes: Any) -> Record | None:
    if int(changes.get("is_current", 0)) == 1:
        clear_current_ability_state(connection, exclude_id=ability_state_id)
    return _update(connection, "ability_state", ability_state_id, changes)


def clear_current_ability_state(
    connection: sqlite3.Connection,
    *,
    exclude_id: int | None = None,
) -> None:
    if exclude_id is None:
        connection.execute("UPDATE ability_state SET is_current = 0 WHERE is_current = 1")
        return
    connection.execute(
        "UPDATE ability_state SET is_current = 0 WHERE is_current = 1 AND id != ?",
        (exclude_id,),
    )


def get_ability_state(connection: sqlite3.Connection, ability_state_id: int) -> Record | None:
    return _fetch_by_id(connection, "ability_state", ability_state_id)


def get_current_ability_state(connection: sqlite3.Connection) -> Record | None:
    return _fetch_one(
        connection,
        "ability_state",
        "SELECT * FROM ability_state WHERE is_current = 1 ORDER BY created_at DESC, id DESC LIMIT 1",
        (),
    )


def get_latest_ability_state_through(connection: sqlite3.Connection, through_date: str) -> Record | None:
    return _fetch_one(
        connection,
        "ability_state",
        """
        SELECT *
        FROM ability_state
        WHERE state_date <= ?
        ORDER BY state_date DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (through_date,),
    )


def create_weekly_report(connection: sqlite3.Connection, **weekly_report: Any) -> int:
    return _insert(connection, "weekly_reports", weekly_report)


def get_weekly_report(connection: sqlite3.Connection, weekly_report_id: int) -> Record | None:
    return _fetch_by_id(connection, "weekly_reports", weekly_report_id)


def get_weekly_report_by_week(connection: sqlite3.Connection, week_id: str) -> Record | None:
    return _fetch_one(
        connection,
        "weekly_reports",
        "SELECT * FROM weekly_reports WHERE week_id = ?",
        (week_id,),
    )


def update_weekly_report(connection: sqlite3.Connection, weekly_report_id: int, **changes: Any) -> Record | None:
    return _update(connection, "weekly_reports", weekly_report_id, changes)


def list_weekly_reports_between(
    connection: sqlite3.Connection,
    start_date: str,
    end_date: str,
) -> list[Record]:
    return _fetch_all(
        connection,
        "weekly_reports",
        """
        SELECT *
        FROM weekly_reports
        WHERE week_end_date >= ? AND week_start_date <= ?
        ORDER BY week_start_date DESC, id DESC
        """,
        (start_date, end_date),
    )


def create_weekly_report_version(connection: sqlite3.Connection, **version: Any) -> int:
    return _insert(connection, "weekly_report_versions", version)


def list_weekly_report_versions(connection: sqlite3.Connection, weekly_report_id: int) -> list[Record]:
    return _fetch_all(
        connection,
        "weekly_report_versions",
        """
        SELECT *
        FROM weekly_report_versions
        WHERE weekly_report_id = ?
        ORDER BY version_no
        """,
        (weekly_report_id,),
    )


def create_weekly_focus(connection: sqlite3.Connection, **weekly_focus: Any) -> int:
    return _insert(connection, "weekly_focus", weekly_focus)


def get_weekly_focus(connection: sqlite3.Connection, weekly_focus_id: int) -> Record | None:
    return _fetch_by_id(connection, "weekly_focus", weekly_focus_id)


def update_weekly_focus(connection: sqlite3.Connection, weekly_focus_id: int, **changes: Any) -> Record | None:
    return _update(connection, "weekly_focus", weekly_focus_id, changes)


def list_weekly_focus_for_report(connection: sqlite3.Connection, weekly_report_id: int) -> list[Record]:
    return _fetch_all(
        connection,
        "weekly_focus",
        "SELECT * FROM weekly_focus WHERE weekly_report_id = ? ORDER BY focus_order",
        (weekly_report_id,),
    )


def delete_weekly_focus_for_report(connection: sqlite3.Connection, weekly_report_id: int) -> None:
    connection.execute("DELETE FROM weekly_focus WHERE weekly_report_id = ?", (weekly_report_id,))


def list_weekly_focus_by_target_week(
    connection: sqlite3.Connection,
    target_week_id: str,
    *,
    status: str | None = "active",
) -> list[Record]:
    sql = "SELECT * FROM weekly_focus WHERE target_week_id = ?"
    params: list[Any] = [target_week_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY priority DESC, focus_order"
    return _fetch_all(connection, "weekly_focus", sql, params)


def list_recent_weekly_focus(
    connection: sqlite3.Connection,
    *,
    limit: int = 5,
    status: str | None = "active",
) -> list[Record]:
    sql = "SELECT * FROM weekly_focus"
    params: list[Any] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, priority DESC, focus_order LIMIT ?"
    params.append(limit)
    return _fetch_all(connection, "weekly_focus", sql, params)


def get_goal_with_active_version_by_date(connection: sqlite3.Connection, goal_date: str) -> Record | None:
    daily_goal = get_daily_goal_by_date(connection, goal_date)
    if daily_goal is None:
        return None
    active_version = None
    if daily_goal["active_version_id"] is not None:
        active_version = get_goal_version(connection, int(daily_goal["active_version_id"]))
    return {
        "daily_goal": daily_goal,
        "project": get_project(connection, int(daily_goal["project_id"])),
        "active_version": active_version,
    }


def get_goal_with_active_version_by_date_and_project(
    connection: sqlite3.Connection,
    goal_date: str,
    project_id: int,
) -> Record | None:
    daily_goal = get_daily_goal_by_date_and_project(connection, goal_date, project_id)
    if daily_goal is None:
        return None
    active_version = None
    if daily_goal["active_version_id"] is not None:
        active_version = get_goal_version(connection, int(daily_goal["active_version_id"]))
    return {
        "daily_goal": daily_goal,
        "project": get_project(connection, int(daily_goal["project_id"])),
        "active_version": active_version,
    }


def list_goal_records_by_date(connection: sqlite3.Connection, goal_date: str) -> list[Record]:
    records: list[Record] = []
    for daily_goal in list_daily_goals_by_date(connection, goal_date):
        active_version = None
        if daily_goal["active_version_id"] is not None:
            active_version = get_goal_version(connection, int(daily_goal["active_version_id"]))
        records.append(
            {
                "daily_goal": daily_goal,
                "project": get_project(connection, int(daily_goal["project_id"])),
                "active_version": active_version,
            }
        )
    return records


def get_workweek_records(connection: sqlite3.Connection, week_id: str) -> list[Record]:
    records: list[Record] = []
    for daily_goal in list_daily_goals_by_week(connection, week_id, monday_to_friday_only=True):
        daily_goal_id = int(daily_goal["id"])
        checkin = _fetch_one(
            connection,
            "daily_checkins",
            "SELECT * FROM daily_checkins WHERE daily_goal_id = ?",
            (daily_goal_id,),
        )
        records.append(
            {
                "daily_goal": daily_goal,
                "project": get_project(connection, int(daily_goal["project_id"])),
                "active_version": (
                    get_goal_version(connection, int(daily_goal["active_version_id"]))
                    if daily_goal["active_version_id"] is not None
                    else None
                ),
                "goal_versions": list_goal_versions(connection, daily_goal_id),
                "daily_checkin": checkin,
                "feedback_messages": list_feedback_messages_for_goal(connection, daily_goal_id),
            }
        )
    return records


def _ensure_default_project(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM projects WHERE status = 'active' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is not None:
        return int(row["id"])
    row = connection.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        return int(row["id"])
    return create_project(
        connection,
        name="DayPilot 榛樿椤圭洰",
        priority="P2",
        role="active",
        status="active",
        project_state=normalize_project_state(
            {},
            updated_from={"source": "repository_default"},
        ),
    )


def _prepare_project_record(
    record: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None = None,
    for_insert: bool = False,
) -> dict[str, Any]:
    data = dict(record)
    legacy_summary_present = "status_summary" in data
    legacy_planning_present = "planning_bias" in data
    legacy_payload_present = "source_payload" in data
    legacy_summary = data.pop("status_summary", None)
    legacy_planning = data.pop("planning_bias", None)
    legacy_payload = data.pop("source_payload", None)
    state_present = "project_state" in data
    if not (for_insert or state_present or legacy_summary_present or legacy_planning_present or legacy_payload_present):
        return data

    raw_state = data.pop("project_state", existing.get("project_state") if existing else None)
    if legacy_summary_present or legacy_planning_present or legacy_payload_present:
        state = project_state_from_legacy(
            status_summary=legacy_summary if legacy_summary_present else None,
            planning_bias=legacy_planning if legacy_planning_present else None,
            source_payload=legacy_payload if legacy_payload_present else None,
            existing_state=raw_state,
        )
    else:
        state = normalize_project_state(raw_state)
    data["project_state"] = state
    return data


def _insert(connection: sqlite3.Connection, table: str, record: Mapping[str, Any]) -> int:
    data = dict(record)
    _validate_columns(table, data.keys())
    columns = list(data.keys())
    if not columns:
        raise ValueError(f"Cannot insert an empty {table} record.")

    placeholders = ", ".join("?" for _ in columns)
    column_names = ", ".join(columns)
    values = [_encode_value(table, column, data[column]) for column in columns]
    cursor = connection.execute(
        f"INSERT INTO {table} ({column_names}) VALUES ({placeholders})",
        values,
    )
    return int(data.get("id") or cursor.lastrowid)


def _update(
    connection: sqlite3.Connection,
    table: str,
    record_id: int,
    changes: Mapping[str, Any],
) -> Record | None:
    data = dict(changes)
    if not data:
        return _fetch_by_id(connection, table, record_id)
    _validate_columns(table, data.keys())
    if table in UPDATED_AT_TABLES and "updated_at" not in data:
        data["updated_at"] = _now_text()

    assignments = ", ".join(f"{column} = ?" for column in data)
    values = [_encode_value(table, column, value) for column, value in data.items()]
    values.append(record_id)
    connection.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", values)
    return _fetch_by_id(connection, table, record_id)


def _fetch_by_id(connection: sqlite3.Connection, table: str, record_id: int) -> Record | None:
    return _fetch_one(connection, table, f"SELECT * FROM {table} WHERE id = ?", (record_id,))


def _fetch_one(
    connection: sqlite3.Connection,
    table: str,
    sql: str,
    params: Iterable[Any],
) -> Record | None:
    row = connection.execute(sql, tuple(params)).fetchone()
    if row is None:
        return None
    return _decode_row(table, row)


def _fetch_all(
    connection: sqlite3.Connection,
    table: str,
    sql: str,
    params: Iterable[Any],
) -> list[Record]:
    return [_decode_row(table, row) for row in connection.execute(sql, tuple(params)).fetchall()]


def _encode_value(table: str, column: str, value: Any) -> Any:
    if column in JSON_FIELDS.get(table, set()) and not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return int(value)
    return value


def _decode_row(table: str, row: sqlite3.Row) -> Record:
    record = dict(row)
    for column in JSON_FIELDS.get(table, set()):
        if column in record and record[column] is not None:
            record[column] = json.loads(record[column])
    if table == "projects":
        record = _materialize_project_record(record)
    return record


def _materialize_project_record(record: Record) -> Record:
    state = normalize_project_state(record.get("project_state"))
    record["project_state"] = state
    record["status_summary"] = state["summary"]
    record["planning_bias"] = state["planning_guidance"]
    updated_from = state.get("updated_from") if isinstance(state.get("updated_from"), dict) else {}
    record["source_payload"] = {
        "project_state": state,
        "progress": state["summary"],
        "planning_bias": state["planning_guidance"],
        "target_goal": state["target_goal"],
        "name": record.get("name") or "",
        "source": updated_from.get("source") or "derived_project_state",
    }
    return record


def _decode_json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _normalize_project_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    facts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        fact = dict(item)
        fact_type = str(fact.get("type") or "context").strip()
        fact["type"] = fact_type if fact_type in PROJECT_FACT_TYPES else "context"
        text = str(fact.get("text") or fact.get("summary") or "").strip()
        if text:
            fact["text"] = text
        fact.pop("summary", None)
        facts.append(fact)
    return facts


def _validate_columns(table: str, columns: Iterable[str]) -> None:
    unknown = set(columns) - TABLE_COLUMNS[table]
    if unknown:
        unknown_columns = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown column(s) for {table}: {unknown_columns}")


def _week_id(day: date) -> str:
    iso_year, iso_week, _ = day.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_id_for_date(day: date | str) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return _week_id(day)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
