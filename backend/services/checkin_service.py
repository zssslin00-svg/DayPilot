from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.services.difficulty_controller import update_ability_state_after_checkin
from backend.services.workday_policy import is_workday


@dataclass(frozen=True)
class CheckinResult:
    checkin: dict[str, Any]
    saved: bool
    updated: bool
    can_generate_weekly_report: bool
    updated_difficulty: dict[str, Any]
    weekly_report_refresh: dict[str, Any]
    project_progress_update: dict[str, Any]
    next_goal_policy: dict[str, Any]


class CheckinValidationError(ValueError):
    """Raised when a check-in request is outside the MVP contract."""


class CheckinPersistenceError(RuntimeError):
    """Raised when a valid check-in request cannot be saved."""


def save_daily_checkin(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    default_date: date,
) -> CheckinResult:
    checkin_date = _parse_checkin_date(request_body.get("date"), default_date)
    if not is_workday(checkin_date):
        raise CheckinValidationError("非工作日不能提交每日 check-in。")

    completion_text = str(request_body.get("completion_text") or "").strip()
    felt_difficulty = _parse_felt_difficulty(request_body.get("felt_difficulty"))
    completion_status = _parse_completion_status(request_body.get("completion_status"))
    tomorrow_direction = str(request_body.get("tomorrow_direction") or "").strip() or None
    requested_goal_id = request_body.get("goal_id")
    weekly_report_refresh: dict[str, Any] = {"status": "not_applicable"}
    project_progress_update: dict[str, Any] = {"status": "not_applicable"}

    connection = initialize_database(db_path)
    try:
        with connection:
            daily_goal = _resolve_daily_goal(connection, checkin_date, requested_goal_id)
            existing = _get_existing_checkin_for_goal(connection, int(daily_goal["id"]))
            if existing is not None and not _checkin_created_on(existing, default_date):
                raise CheckinValidationError("check-in 只可在提交当天修改，已过期的历史记录仅展示最新可用版本。")
            record = {
                "daily_goal_id": daily_goal["id"],
                "checkin_date": checkin_date.isoformat(),
                "week_id": repo.week_id_for_date(checkin_date),
                "is_workday": 1,
                "completion_status": completion_status,
                "completion_text": completion_text,
                "felt_difficulty": felt_difficulty,
                "tomorrow_direction": tomorrow_direction,
                "parsed_completion_rate": None,
                "completed_items": [],
                "unfinished_items": [],
                "blockers": [],
                "actual_outputs": [],
                "processor_snapshot": {
                    "status": "not_parsed",
                    "reason": "completion parsing is implemented in difficulty_controller",
                },
            }

            if existing is None:
                record["created_at"] = _checkin_timestamp_for_date(default_date)
                checkin_id = repo.create_daily_checkin(connection, **record)
                updated = False
            else:
                checkin_id = int(existing["id"])
                repo.update_daily_checkin(connection, checkin_id, **record)
                updated = True

            difficulty_result = update_ability_state_after_checkin(connection, checkin_id)
            checkin = repo.get_daily_checkin(connection, checkin_id)
            if checkin is None:
                raise CheckinPersistenceError("check-in 保存后无法读取。")
            _update_weekly_focus_after_checkin(connection, checkin)
            existing_weekly_report = repo.get_weekly_report_by_week(connection, str(checkin["week_id"]))
            should_refresh_weekly_report = updated and existing_weekly_report is not None
            can_generate_weekly_report = (
                checkin_date.isoweekday() == 5 and _all_goals_for_date_checked_in(connection, checkin_date)
            )
            next_goal_policy = _next_goal_policy(checkin)

        project_progress_update = _update_project_progress_after_checkin(
            db_path,
            int(checkin["id"]),
        )

        if should_refresh_weekly_report:
            weekly_report_refresh = _refresh_weekly_report_after_checkin_edit(
                db_path,
                str(checkin["week_id"]),
                checkin_date,
            )

        return CheckinResult(
            checkin=checkin,
            saved=True,
            updated=updated,
            can_generate_weekly_report=can_generate_weekly_report,
            updated_difficulty={
                "ability_state": difficulty_result.ability_state,
                "completion_parse_result": difficulty_result.completion_parse_result,
                "difficulty_update_event": difficulty_result.difficulty_update_event,
            },
            weekly_report_refresh=weekly_report_refresh,
            project_progress_update=project_progress_update,
            next_goal_policy=next_goal_policy,
        )
    finally:
        connection.close()


def _update_project_progress_after_checkin(
    db_path: str | Path,
    checkin_id: int,
) -> dict[str, Any]:
    from backend.services.project_progress_service import update_project_progress_for_checkin

    try:
        result = update_project_progress_for_checkin(db_path, checkin_id)
    except Exception as exc:  # noqa: BLE001 - check-in save must not be rolled back
        return {
            "status": "failed",
            "reason": str(exc),
        }
    return result.payload


def _refresh_weekly_report_after_checkin_edit(
    db_path: str | Path,
    week_id: str,
    checkin_date: date,
) -> dict[str, Any]:
    from backend.services.weekly_report_service import generate_weekly_report

    try:
        result = generate_weekly_report(
            db_path,
            {"week_id": week_id},
            default_date=checkin_date,
            revision_source="checkin_refresh",
            revision_reason=f"Check-in edited for {checkin_date.isoformat()}.",
        )
    except Exception as exc:  # noqa: BLE001 - check-in save must not be rolled back by report refresh
        return {
            "status": "failed",
            "week_id": week_id,
            "reason": str(exc),
        }
    return {
        "status": "refreshed",
        "week_id": week_id,
        "weekly_report_id": result.weekly_report["id"],
        "version_count": len(result.weekly_report_versions),
    }


def _resolve_daily_goal(
    connection,
    checkin_date: date,
    requested_goal_id: Any,
) -> dict[str, Any]:
    if requested_goal_id in (None, ""):
        raise CheckinValidationError("多项目模式下提交 check-in 必须提供 goal_id。")
    try:
        goal_id = int(requested_goal_id)
    except (TypeError, ValueError) as exc:
        raise CheckinValidationError("goal_id 必须是整数。") from exc
    daily_goal = repo.get_daily_goal(connection, goal_id)
    if daily_goal is None:
        raise CheckinValidationError("指定的 goal_id 不存在。")
    if daily_goal["goal_date"] != checkin_date.isoformat():
        raise CheckinValidationError("goal_id 与 check-in 日期不一致。")
    return daily_goal


def _get_existing_checkin_for_goal(connection, daily_goal_id: int) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM daily_checkins WHERE daily_goal_id = ?",
        (daily_goal_id,),
    ).fetchone()


def _checkin_created_on(checkin: dict[str, Any], current_date: date) -> bool:
    try:
        raw_created_at = checkin["created_at"]
    except (KeyError, IndexError, TypeError):
        raw_created_at = None
    created_at = str(raw_created_at or "").strip()
    if len(created_at) < 10:
        return False
    try:
        created_date = date.fromisoformat(created_at[:10])
    except ValueError:
        return False
    return created_date == current_date


def _checkin_timestamp_for_date(value: date) -> str:
    return f"{value.isoformat()} 00:00:00"


def _all_goals_for_date_checked_in(connection, checkin_date: date) -> bool:
    goals = repo.list_daily_goals_by_date(connection, checkin_date.isoformat())
    if not goals:
        return False
    for goal in goals:
        checkin = _get_existing_checkin_for_goal(connection, int(goal["id"]))
        if checkin is None:
            return False
    return True


def _parse_checkin_date(value: Any, default_date: date) -> date:
    if value in (None, ""):
        return default_date
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CheckinValidationError("date 必须是 YYYY-MM-DD。") from exc


def _parse_felt_difficulty(value: Any) -> int:
    try:
        difficulty = int(value)
    except (TypeError, ValueError) as exc:
        raise CheckinValidationError("felt_difficulty 必须是 1-5 的整数。") from exc
    if difficulty < 1 or difficulty > 5:
        raise CheckinValidationError("felt_difficulty 必须在 1-5 之间。")
    return difficulty


def _parse_completion_status(value: Any) -> str:
    status = str(value or "").strip()
    if status not in {"completed", "incomplete"}:
        raise CheckinValidationError("completion_status 必须是 completed 或 incomplete。")
    return status


def _next_goal_policy(checkin: dict[str, Any]) -> dict[str, Any]:
    completed = str(checkin.get("completion_status") or "") == "completed"
    return {
        "project_goal_id": checkin["daily_goal_id"],
        "policy": "generate_new" if completed else "carry_over",
        "reason": "用户显式选择完成。" if completed else "用户显式选择未完成，次工作日继续承接。",
    }


def _update_weekly_focus_after_checkin(connection, checkin: dict[str, Any]) -> None:
    daily_goal = repo.get_daily_goal(connection, int(checkin["daily_goal_id"]))
    if daily_goal is None:
        return
    snapshot = daily_goal.get("context_snapshot") or {}
    selected_focus_id = snapshot.get("selected_weekly_focus_id")
    if selected_focus_id in (None, ""):
        return

    weekly_focus = repo.get_weekly_focus(connection, int(selected_focus_id))
    if weekly_focus is None:
        return

    payload = dict(weekly_focus.get("context_payload") or {})
    handoff = dict(payload.get("handoff") or {})
    progress_history = list(handoff.get("progress_history") or [])
    completion_rate = _float_or_zero(checkin.get("parsed_completion_rate"))
    felt_difficulty = int(checkin.get("felt_difficulty") or 0)
    status_after_checkin = _focus_status_after_checkin(checkin, completion_rate)
    progress_score = max(_float_or_zero(handoff.get("progress_score")), completion_rate)
    next_day_strategy = _next_focus_strategy(status_after_checkin, completion_rate, felt_difficulty)

    entry = {
        "checkin_id": checkin["id"],
        "daily_goal_id": checkin["daily_goal_id"],
        "checkin_date": checkin["checkin_date"],
        "completion_rate": completion_rate,
        "felt_difficulty": felt_difficulty,
        "status_after_checkin": status_after_checkin,
        "next_day_strategy": next_day_strategy,
    }
    progress_history = [
        item for item in progress_history if int(item.get("checkin_id") or 0) != int(checkin["id"])
    ]
    progress_history.append(entry)

    handoff.update(
        {
            "last_checkin_date": checkin["checkin_date"],
            "last_checkin_id": checkin["id"],
            "progress_score": round(min(progress_score, 1.0), 3),
            "status_after_checkin": status_after_checkin,
            "next_day_strategy": next_day_strategy,
            "progress_history": progress_history[-5:],
        }
    )
    payload["handoff"] = handoff
    repo.update_weekly_focus(connection, int(weekly_focus["id"]), context_payload=payload)


def _focus_status_after_checkin(checkin: dict[str, Any], completion_rate: float) -> str:
    if str(checkin.get("completion_status") or "") == "completed":
        return "completed"
    blockers = checkin.get("blockers") if isinstance(checkin.get("blockers"), list) else []
    completion_text = str(checkin.get("completion_text") or "")
    if blockers or any(token in completion_text for token in ("卡住", "阻塞", "依赖", "权限")):
        return "blocked"
    if completion_rate >= 0.85:
        return "completed"
    return "in_progress"


def _next_focus_strategy(status_after_checkin: str, completion_rate: float, felt_difficulty: int) -> str:
    if status_after_checkin == "completed":
        return "select_next_focus_or_validate"
    if status_after_checkin == "blocked":
        return "generate_unblock_slice"
    if completion_rate < 0.4 and felt_difficulty >= 4:
        return "continue_same_focus_with_smaller_slice"
    if completion_rate < 0.4 and felt_difficulty <= 2:
        return "check_direction_conflict_before_continuing"
    return "continue_same_focus"


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
