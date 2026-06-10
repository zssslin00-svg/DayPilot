from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import initialize_database
from backend.schemas.json_schema import JsonSchemaValidationError
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH
from backend.services.weekly_report_memory_service import (
    apply_weekly_report_memory_from_feedback,
    weekly_report_preferences_from_profile,
)
from backend.services.weekly_report_resources import validate_weekly_report_output


PROMPT_VERSION_MOCK = "weekly_report_v1_mock"
PROMPT_VERSION_DEEPSEEK = "weekly_report_v2_deepseek"
PROMPT_VERSION_FEEDBACK_MOCK = "weekly_report_feedback_v1_mock"
PROMPT_VERSION_FEEDBACK_DEEPSEEK = "weekly_report_feedback_v2_deepseek"
MOCK_MODEL_NAME = "mock-weekly-report-adapter"

VAGUE_PHRASES = [
    "继续完善相关工作",
    "持续优化能力",
    "加强学习和思考",
    "推进各项任务",
    "完成了很多工作",
    "下周继续开发",
    "总体表现不错",
]
WEEKDAY_WORDS = ["周一", "周二", "周三", "周四", "周五", "星期一", "星期二", "星期三", "星期四", "星期五"]
OUTCOME_VERBS = ["完成", "交付", "跑通", "形成", "验证", "补齐", "定稿", "收敛"]
RESULT_WORDS = ["闭环", "结果", "记录", "接口", "页面", "测试", "样例", "报告", "周报", "重点", "版本", "产出", "规则"]


@dataclass(frozen=True)
class WeeklyReportResult:
    weekly_report: dict[str, Any]
    report_output: dict[str, list[str]]
    weekly_focus: list[dict[str, Any]]
    source_snapshot: dict[str, Any]
    weekly_report_versions: list[dict[str, Any]]
    created: bool
    weekly_report_memory_update: dict[str, Any] | None = None


class WeeklyReportValidationError(ValueError):
    """Raised when weekly report generation is requested before the weekly trigger is ready."""


class WeeklyReportGenerationError(RuntimeError):
    """Raised when the weekly report cannot be generated or persisted."""


def generate_weekly_report(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    default_date: date,
    revision_source: str | None = None,
    revision_reason: str | None = None,
    feedback_message: str | None = None,
) -> WeeklyReportResult:
    week_id = _parse_week_id(request_body.get("week_id"), default_date)
    connection = initialize_database(db_path)
    try:
        with connection:
            snapshot = build_weekly_snapshot(connection, week_id, generated_on=default_date)
            if not snapshot["trigger_status"]["friday_checkin_submitted"]:
                raise WeeklyReportValidationError("周报需要在周五 check-in 后生成。")

            existing = repo.get_weekly_report_by_week(connection, week_id)
            current_report_output = report_output_from_record(existing) if existing else None
            llm_result = generate_json_with_fallback(
                task_name="weekly_report_feedback_revision" if feedback_message else "weekly_report_generation",
                prompt_version_deepseek=(
                    PROMPT_VERSION_FEEDBACK_DEEPSEEK if feedback_message else PROMPT_VERSION_DEEPSEEK
                ),
                prompt_version_mock=(
                    PROMPT_VERSION_FEEDBACK_MOCK if feedback_message else PROMPT_VERSION_MOCK
                ),
                mock_model_name=MOCK_MODEL_NAME,
                build_messages=lambda soul: (
                    _weekly_report_feedback_messages(
                        snapshot,
                        current_report_output or {},
                        feedback_message or "",
                        soul,
                    )
                    if feedback_message
                    else _weekly_report_messages(snapshot, soul)
                ),
                mock_generate=lambda: (
                    _mock_weekly_report_feedback(
                        current_report_output or {},
                        snapshot,
                        feedback_message or "",
                    )
                    if feedback_message
                    else MockWeeklyReportLLMAdapter().generate(snapshot)
                ),
                validator=validate_weekly_report_output,
            )
            report_output = llm_result.output
            quality_review = review_weekly_report(report_output, snapshot)
            if not quality_review["passed"]:
                report_output = repair_weekly_report(report_output, snapshot)
                quality_review = review_weekly_report(report_output, snapshot)
            if not quality_review["passed"]:
                details = "; ".join(quality_review["failures"])
                raise WeeklyReportGenerationError(f"周报质量审查未通过：{details}")

            try:
                validate_weekly_report_output(report_output)
            except JsonSchemaValidationError as exc:
                raise WeeklyReportGenerationError(str(exc)) from exc

            report_text = render_weekly_report_text(report_output)
            focus_candidates = extract_weekly_focus(report_output, snapshot, llm_result.metadata)
            source_snapshot = dict(snapshot["source_snapshot"])
            source_snapshot["llm_metadata"] = llm_result.metadata
            source_snapshot["weekly_report_preferences"] = snapshot["weekly_report_preferences"]

            weekly_report_payload = {
                "week_id": week_id,
                "week_start_date": snapshot["week_start_date"],
                "week_end_date": snapshot["week_end_date"],
                "generated_on_date": default_date.isoformat(),
                "status": "regenerated" if existing else "final",
                "completed_work": _render_bullets(report_output["completed_work"]),
                "next_week_plan": _render_bullets(report_output["next_week_plan"]),
                "weekly_reflection": _render_bullets(report_output["weekly_reflection"]),
                "report_text": report_text,
                "source_snapshot": source_snapshot,
                "next_week_focus_summary": "；".join(item["focus_text"] for item in focus_candidates[:3]),
                "quality_score": None,
                "prompt_version": llm_result.metadata["prompt_version"],
                "model_name": llm_result.metadata["model_name"],
            }

            if existing is None:
                weekly_report_id = repo.create_weekly_report(connection, **weekly_report_payload)
                created = True
            else:
                weekly_report_id = int(existing["id"])
                _ensure_current_weekly_report_version(connection, existing)
                repo.update_weekly_report(connection, weekly_report_id, **weekly_report_payload)
                repo.delete_weekly_focus_for_report(connection, weekly_report_id)
                created = False

            for focus in focus_candidates:
                repo.create_weekly_focus(
                    connection,
                    weekly_report_id=weekly_report_id,
                    source_week_id=week_id,
                    target_week_id=snapshot["target_week_id"],
                    focus_order=focus["focus_order"],
                    focus_text=focus["focus_text"],
                    desired_outcome=focus["desired_outcome"],
                    focus_type=focus["focus_type"],
                    priority=focus["priority"],
                    status="active",
                    context_payload=focus["context_payload"],
                )

            weekly_report = repo.get_weekly_report(connection, weekly_report_id)
            weekly_focus = repo.list_weekly_focus_for_report(connection, weekly_report_id)
            if weekly_report is None:
                raise WeeklyReportGenerationError("周报保存后无法读取。")
            _create_weekly_report_version(
                connection,
                weekly_report=weekly_report,
                report_output=report_output,
                source_snapshot=source_snapshot,
                llm_metadata=llm_result.metadata,
                revision_source=revision_source
                or ("initial_generation" if created else "manual_regeneration"),
                revision_reason=revision_reason,
                feedback_message=feedback_message,
            )
            weekly_report_versions = [
                {
                    **version,
                    "report_output": report_output_from_record(version),
                }
                for version in repo.list_weekly_report_versions(connection, weekly_report_id)
            ]

        return WeeklyReportResult(
            weekly_report=weekly_report,
            report_output=report_output,
            weekly_focus=weekly_focus,
            source_snapshot=source_snapshot,
            weekly_report_versions=weekly_report_versions,
            created=created,
        )
    except sqlite3.DatabaseError as exc:
        raise WeeklyReportGenerationError(str(exc)) from exc
    finally:
        connection.close()


def regenerate_weekly_report_from_feedback(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    default_date: date,
    soul_path: str | Path = SOUL_PATH,
) -> WeeklyReportResult:
    week_id = _parse_week_id(request_body.get("week_id"), default_date)
    message = str(request_body.get("message") or "").strip()
    if not message:
        raise WeeklyReportValidationError("message 不能为空。")

    connection = initialize_database(db_path)
    try:
        existing = repo.get_weekly_report_by_week(connection, week_id)
        if existing is None:
            raise WeeklyReportValidationError("该周还没有可修改的周报。")
    finally:
        connection.close()

    try:
        result = generate_weekly_report(
            db_path,
            {"week_id": week_id},
            default_date=default_date,
            revision_source="user_feedback",
        revision_reason="根据用户周报修改意见重新生成。",
            feedback_message=message,
        )
    except WeeklyReportGenerationError:
        apply_weekly_report_memory_from_feedback(
            db_path,
            week_id=week_id,
            feedback_message=message,
            soul_path=soul_path,
        )
        raise
    memory_update = apply_weekly_report_memory_from_feedback(
        db_path,
        week_id=week_id,
        feedback_message=message,
        soul_path=soul_path,
    ).payload
    return replace(result, weekly_report_memory_update=memory_update)


def build_weekly_snapshot(connection, week_id: str, *, generated_on: date) -> dict[str, Any]:
    week_start, week_end = _week_bounds(week_id)
    records = repo.get_workweek_records(connection, week_id)
    if not records:
        raise WeeklyReportValidationError("该 week_id 没有可聚合的工作日记录。")

    profile = repo.get_user_profile(connection)
    weekly_report_preferences = weekly_report_preferences_from_profile(profile)
    ability_state = (
        repo.get_latest_ability_state_through(connection, week_end.isoformat())
        or repo.get_current_ability_state(connection)
    )
    daily_records = [_normalize_daily_record(item) for item in records]
    friday_checkin_submitted = any(
        item["weekday"] == 5 and item["checkin"] is not None for item in daily_records
    )
    feedback_ids = [
        feedback["id"]
        for item in records
        for feedback in item.get("feedback_messages", [])
    ]
    active_version_ids = [
        item["active_version"]["id"]
        for item in records
        if item.get("active_version") is not None
    ]
    checkin_ids = [
        item["daily_checkin"]["id"]
        for item in records
        if item.get("daily_checkin") is not None
    ]
    goal_version_ids = [
        version["id"]
        for item in records
        for version in item.get("goal_versions", [])
    ]
    source_snapshot = {
        "schema_version": "weekly_report_source_snapshot.v1",
        "week_id": week_id,
        "daily_goal_ids": [item["daily_goal"]["id"] for item in records],
        "active_version_ids": active_version_ids,
        "goal_version_ids": goal_version_ids,
        "checkin_ids": checkin_ids,
        "feedback_message_ids": feedback_ids,
        "ability_state_id": ability_state["id"] if ability_state else None,
        "friday_checkin_submitted": friday_checkin_submitted,
        "generated_on_date": generated_on.isoformat(),
    }
    return {
        "week_id": week_id,
        "week_start_date": week_start.isoformat(),
        "week_end_date": week_end.isoformat(),
        "target_week_id": repo.week_id_for_date(date.fromordinal(week_start.toordinal() + 7)),
        "trigger_status": {
            "friday_checkin_submitted": friday_checkin_submitted,
            "generated_on": generated_on.isoformat(),
            "generation_mode": "friday_generate" if generated_on.isoweekday() == 5 else "manual_generate",
        },
        "user_profile": profile or {},
        "weekly_report_preferences": weekly_report_preferences,
        "ability_state": ability_state,
        "daily_records": daily_records,
        "week_unfinished_items": _week_unfinished_items(daily_records),
        "revision_summary": _revision_summary(records),
        "difficulty_summary": _difficulty_summary(daily_records),
        "next_week_direction_candidates": _next_week_direction_candidates(daily_records),
        "source_snapshot": source_snapshot,
    }


class MockWeeklyReportLLMAdapter:
    def generate(self, snapshot: dict[str, Any]) -> dict[str, list[str]]:
        completed_work = _unique_sentences(self._completed_work(snapshot), limit=4)
        next_week_plan = _unique_sentences(self._next_week_plan(snapshot), limit=4)
        weekly_reflection = _unique_sentences(self._weekly_reflection(snapshot), limit=4)
        return {
            "completed_work": _pad_section(
                completed_work,
                [
                    "形成本周 DayPilot 工作记录，支持周报生成引用。",
                    "跑通本周目标与打卡数据的基础闭环。",
                ],
            ),
            "next_week_plan": _pad_section(
                next_week_plan,
                [
                    "完成下周重点承接的最小可验证闭环。",
                    "补齐周报生成结果的回归测试记录。",
                ],
            ),
            "weekly_reflection": _pad_section(
                weekly_reflection,
                [
                    "本周需要继续压缩目标范围，优先保留最低版本。",
                    "下周应先验证主链路，再扩展新的智能能力。",
                ],
            ),
        }

    def _completed_work(self, snapshot: dict[str, Any]) -> list[str]:
        bullets: list[str] = []
        for record in snapshot["daily_records"]:
            checkin = record["checkin"]
            if checkin is None:
                continue
            rate = float(checkin.get("parsed_completion_rate") or 0)
            if rate < 0.65 and not _looks_completed(checkin.get("completion_text", "")):
                continue
            summary = _best_completed_summary(record)
            if summary:
                bullets.append(_short_sentence(f"完成{summary}，保留可复查产出。", 60))
        return bullets

    def _next_week_plan(self, snapshot: dict[str, Any]) -> list[str]:
        bullets: list[str] = []
        for item in snapshot["week_unfinished_items"][:3]:
            bullets.append(_short_sentence(f"完成{_strip_weak_verbs(item)}的可验收闭环。", 60))
        for direction in snapshot["next_week_direction_candidates"][:3]:
            bullets.append(_short_sentence(f"交付{_strip_weak_verbs(direction)}的最小可验证结果。", 60))
        return bullets

    def _weekly_reflection(self, snapshot: dict[str, Any]) -> list[str]:
        bullets = ["周报只记录有证据的成果，计划事项保留下周承接。"]
        revision_count = int(snapshot["revision_summary"]["revision_count"])
        high_days = int(snapshot["difficulty_summary"]["high_difficulty_days"])
        low_high_days = int(snapshot["difficulty_summary"]["low_completion_high_difficulty_days"])
        if revision_count:
            bullets.append("本周多次通过反馈收敛范围，下周需要更早切出最低版本。")
        if high_days:
            bullets.append("本周高难度天数偏多，下周应减少并行范围。")
        if low_high_days:
            bullets.append("低完成且高难度的记录提醒下周先控规模再加挑战。")
        return bullets


def _weekly_report_messages(snapshot: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot Weekly Report Generator. Return only valid json.
The JSON object must contain completed_work, next_week_plan, and weekly_reflection arrays.
Use concise Chinese. Do not invent completed work that is not supported by the snapshot.
"""
    user = {
        "task": "Generate a weekly report after Friday check-in.",
        "schema": {
            "completed_work": "2-4 evidence-backed bullets",
            "next_week_plan": "2-4 outcome-oriented bullets",
            "weekly_reflection": "2-4 reflection bullets",
        },
        "snapshot": snapshot,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _weekly_report_feedback_messages(
    snapshot: dict[str, Any],
    current_report: dict[str, Any],
    feedback_message: str,
    soul: str,
) -> list[dict[str, str]]:
    system = f"""{soul}

You are the DayPilot Weekly Report Revision Agent. Return only valid json.
Revise the current weekly report according to the user's feedback.
Do not invent completed work that is not supported by the snapshot.
Keep completed_work, next_week_plan, and weekly_reflection as arrays of concise Chinese bullets.
"""
    user = {
        "task": "Revise an existing weekly report.",
        "feedback_message": feedback_message,
        "current_report": current_report,
        "snapshot": snapshot,
        "constraints": [
            "keep facts evidence-backed",
            "make next_week_plan outcome-oriented",
            "do not include weekday-by-weekday logs",
            "return only JSON",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _mock_weekly_report_feedback(
    current_report: dict[str, Any],
    snapshot: dict[str, Any],
    feedback_message: str,
) -> dict[str, list[str]]:
    revised = repair_weekly_report(current_report, snapshot) if current_report else MockWeeklyReportLLMAdapter().generate(snapshot)
    feedback_note = _short_sentence(f"根据修改意见调整周报重点：{_strip_sentence_end(feedback_message)}", 70)
    revised["weekly_reflection"] = _unique_sentences(
        [feedback_note] + revised.get("weekly_reflection", []),
        limit=4,
    )
    return {
        "completed_work": _pad_section(revised.get("completed_work", []), MockWeeklyReportLLMAdapter().generate(snapshot)["completed_work"]),
        "next_week_plan": _pad_section(revised.get("next_week_plan", []), MockWeeklyReportLLMAdapter().generate(snapshot)["next_week_plan"]),
        "weekly_reflection": _pad_section(revised.get("weekly_reflection", []), MockWeeklyReportLLMAdapter().generate(snapshot)["weekly_reflection"]),
    }


def review_weekly_report(report: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": True,
        "failures": [],
        "quality_score": None,
    }
    failures: list[str] = []
    try:
        validate_weekly_report_output(report)
    except JsonSchemaValidationError as exc:
        failures.append(str(exc))

    for section_name, bullets in report.items():
        if not isinstance(bullets, list):
            continue
        for bullet in bullets:
            text = str(bullet)
            if any(phrase in text for phrase in VAGUE_PHRASES):
                failures.append(f"{section_name} 包含空泛表达：{text}")
            if any(word in text for word in WEEKDAY_WORDS):
                failures.append(f"{section_name} 包含按日期流水账表达：{text}")

    evidence_text = _evidence_text(snapshot)
    for bullet in report.get("completed_work", []):
        if not _has_evidence(str(bullet), evidence_text):
            failures.append(f"completed_work 缺少来源证据：{bullet}")

    for bullet in report.get("next_week_plan", []):
        if not _is_outcome_goal(str(bullet)):
            failures.append(f"next_week_plan 不是可验收结果目标：{bullet}")

    reflections = " ".join(str(item) for item in report.get("weekly_reflection", []))
    if not any(word in reflections for word in ["范围", "节奏", "复盘", "调整", "难度", "最低版本", "证据"]):
        failures.append("weekly_reflection 缺少复盘或调整信号。")

    return {
        "passed": not failures,
        "failures": failures,
        "quality_score": 5 if not failures else 2,
    }


def repair_weekly_report(report: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, list[str]]:
    repaired = MockWeeklyReportLLMAdapter().generate(snapshot)
    for key in ("completed_work", "next_week_plan", "weekly_reflection"):
        if key in report and isinstance(report[key], list):
            repaired[key] = _unique_sentences([str(item) for item in report[key]] + repaired[key], limit=4)
    baseline = MockWeeklyReportLLMAdapter().generate(snapshot)
    return {
        "completed_work": _pad_section(repaired["completed_work"], baseline["completed_work"]),
        "next_week_plan": _pad_section(repaired["next_week_plan"], baseline["next_week_plan"]),
        "weekly_reflection": _pad_section(repaired["weekly_reflection"], baseline["weekly_reflection"]),
    }


def extract_weekly_focus(
    report: dict[str, list[str]],
    snapshot: dict[str, Any],
    llm_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    focus_items: list[dict[str, Any]] = []
    metadata = llm_metadata or {}
    for index, plan in enumerate(report["next_week_plan"][:4], start=1):
        focus_text = _strip_sentence_end(_strip_weak_verbs(plan))
        focus_items.append(
            {
                "focus_order": index,
                "focus_text": focus_text,
                "desired_outcome": _short_sentence(focus_text, 90),
                "focus_type": _infer_focus_type(focus_text),
                "priority": max(1, 6 - index),
                "context_payload": {
                    "source": ["weekly_report.next_week_plan"],
                    "success_criteria": [
                        _short_sentence(f"{focus_text}有可检查结果", 60),
                        "保留完成证据并更新下周目标上下文",
                    ],
                    "prompt_version": metadata.get("prompt_version") or PROMPT_VERSION_MOCK,
                    "model_name": metadata.get("model_name") or MOCK_MODEL_NAME,
                    "source_week_id": snapshot["week_id"],
                },
            }
        )
    return focus_items


def render_weekly_report_text(report: dict[str, list[str]]) -> str:
    sections = [
        ("本周完成工作", report["completed_work"]),
        ("下周工作计划及重点", report["next_week_plan"]),
        ("一周工作总结及感悟", report["weekly_reflection"]),
    ]
    return "\n\n".join(f"{title}\n{_render_bullets(items)}" for title, items in sections)


def report_output_from_record(record: dict[str, Any] | None) -> dict[str, list[str]]:
    if not record:
        return {"completed_work": [], "next_week_plan": [], "weekly_reflection": []}
    return {
        "completed_work": _parse_bullets(record.get("completed_work")),
        "next_week_plan": _parse_bullets(record.get("next_week_plan")),
        "weekly_reflection": _parse_bullets(record.get("weekly_reflection")),
    }


def _ensure_current_weekly_report_version(connection, weekly_report: dict[str, Any]) -> None:
    weekly_report_id = int(weekly_report["id"])
    if repo.list_weekly_report_versions(connection, weekly_report_id):
        return
    output = report_output_from_record(weekly_report)
    repo.create_weekly_report_version(
        connection,
        weekly_report_id=weekly_report_id,
        week_id=weekly_report["week_id"],
        version_no=1,
        revision_source="current_snapshot",
        revision_reason="Snapshot before first regeneration.",
        feedback_message=None,
        completed_work=weekly_report["completed_work"],
        next_week_plan=weekly_report["next_week_plan"],
        weekly_reflection=weekly_report["weekly_reflection"],
        report_text=weekly_report["report_text"],
        source_snapshot=weekly_report.get("source_snapshot") or {},
        llm_metadata=(weekly_report.get("source_snapshot") or {}).get("llm_metadata") or {
            "model_name": weekly_report.get("model_name"),
            "prompt_version": weekly_report.get("prompt_version"),
        },
    )
    validate_weekly_report_output(output)


def _create_weekly_report_version(
    connection,
    *,
    weekly_report: dict[str, Any],
    report_output: dict[str, list[str]],
    source_snapshot: dict[str, Any],
    llm_metadata: dict[str, Any],
    revision_source: str,
    revision_reason: str | None,
    feedback_message: str | None,
) -> int:
    weekly_report_id = int(weekly_report["id"])
    version_no = len(repo.list_weekly_report_versions(connection, weekly_report_id)) + 1
    return repo.create_weekly_report_version(
        connection,
        weekly_report_id=weekly_report_id,
        week_id=weekly_report["week_id"],
        version_no=version_no,
        revision_source=revision_source,
        revision_reason=revision_reason,
        feedback_message=feedback_message,
        completed_work=_render_bullets(report_output["completed_work"]),
        next_week_plan=_render_bullets(report_output["next_week_plan"]),
        weekly_reflection=_render_bullets(report_output["weekly_reflection"]),
        report_text=render_weekly_report_text(report_output),
        source_snapshot=source_snapshot,
        llm_metadata=llm_metadata,
    )


def _normalize_daily_record(record: dict[str, Any]) -> dict[str, Any]:
    daily_goal = record["daily_goal"]
    checkin = record.get("daily_checkin")
    return {
        "date": daily_goal["goal_date"],
        "weekday": int(daily_goal["weekday"]),
        "is_workday": bool(daily_goal["is_workday"]),
        "final_active_goal": record.get("active_version"),
        "goal_versions": record.get("goal_versions", []),
        "online_feedback": record.get("feedback_messages", []),
        "checkin": checkin,
    }


def _week_unfinished_items(daily_records: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for record in daily_records:
        checkin = record["checkin"]
        if not checkin:
            continue
        items.extend(_string_items(checkin.get("unfinished_items")))
        text = str(checkin.get("completion_text") or "")
        if _looks_unfinished(text):
            items.append(text)
    return _unique_text(items)[:6]


def _revision_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: list[str] = []
    count = 0
    for record in records:
        versions = record.get("goal_versions", [])
        count += max(0, len(versions) - 1)
        for version in versions:
            reason = str(version.get("revision_reason") or "").strip()
            if reason and reason != "Morning goal creation.":
                reasons.append(reason)
        for feedback in record.get("feedback_messages", []):
            interpretation = feedback.get("interpretation_json") or {}
            summary = interpretation.get("summary")
            if summary is None and isinstance(interpretation.get("interpretation"), dict):
                summary = interpretation["interpretation"].get("summary")
            if summary:
                reasons.append(str(summary))
    return {"revision_count": count, "main_revision_reasons": _unique_text(reasons)[:5]}


def _difficulty_summary(daily_records: list[dict[str, Any]]) -> dict[str, Any]:
    felt_values: list[int] = []
    high_days = 0
    low_high_days = 0
    for record in daily_records:
        checkin = record["checkin"]
        if not checkin:
            continue
        felt = int(checkin.get("felt_difficulty") or 0)
        rate = float(checkin.get("parsed_completion_rate") or 0)
        if felt:
            felt_values.append(felt)
        if felt >= 4:
            high_days += 1
        if felt >= 4 and rate < 0.65:
            low_high_days += 1
    average = round(sum(felt_values) / len(felt_values), 2) if felt_values else None
    return {
        "average_felt_difficulty": average,
        "high_difficulty_days": high_days,
        "low_completion_high_difficulty_days": low_high_days,
    }


def _next_week_direction_candidates(daily_records: list[dict[str, Any]]) -> list[str]:
    directions = [
        str(record["checkin"].get("tomorrow_direction") or "").strip()
        for record in daily_records
        if record["checkin"] is not None and str(record["checkin"].get("tomorrow_direction") or "").strip()
    ]
    return _unique_text(reversed(directions))[:5]


def _best_completed_summary(record: dict[str, Any]) -> str:
    checkin = record["checkin"] or {}
    for source in ("completed_items", "actual_outputs"):
        items = _string_items(checkin.get(source))
        if items:
            return _strip_sentence_end(items[0])
    goal = record.get("final_active_goal") or {}
    main_goal = str(goal.get("main_goal") or "").strip()
    if main_goal:
        return _strip_leading_result_verb(_strip_sentence_end(main_goal))
    return _strip_sentence_end(str(checkin.get("completion_text") or "本周核心目标"))


def _evidence_text(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for record in snapshot["daily_records"]:
        goal = record.get("final_active_goal") or {}
        checkin = record.get("checkin") or {}
        parts.append(str(goal.get("main_goal") or ""))
        parts.append(str(checkin.get("completion_text") or ""))
        parts.extend(_string_items(checkin.get("completed_items")))
        parts.extend(_string_items(checkin.get("actual_outputs")))
    return " ".join(parts)


def _has_evidence(bullet: str, evidence_text: str) -> bool:
    normalized = _strip_sentence_end(bullet)
    for token in _evidence_tokens(normalized):
        if token in evidence_text:
            return True
    return False


def _evidence_tokens(text: str) -> list[str]:
    text = _strip_leading_result_verb(text)
    cleaned = re.sub(r"[，。、；:：.()\s]+", " ", text)
    tokens = [item for item in cleaned.split(" ") if len(item) >= 2]
    if tokens:
        return tokens
    return [text[index : index + 4] for index in range(0, max(0, len(text) - 3), 2)]


def _is_outcome_goal(text: str) -> bool:
    return any(verb in text for verb in OUTCOME_VERBS) and any(word in text for word in RESULT_WORDS)


def _looks_completed(text: str) -> bool:
    return any(word in text for word in ["完成", "交付", "提交", "跑通", "写完", "定稿"])


def _looks_unfinished(text: str) -> bool:
    return any(word in text for word in ["未完成", "没完成", "还没", "待补", "需要明天", "做不完"])


def _strip_weak_verbs(text: str) -> str:
    result = _strip_sentence_end(str(text).strip())
    for prefix in ("继续", "推进", "优化一个", "研究一个", "下周", "明天"):
        result = result.removeprefix(prefix).strip(" ，。")
    return result or "DayPilot 下周重点"


def _strip_leading_result_verb(text: str) -> str:
    result = text.strip()
    for prefix in ("完成", "实现", "补齐", "接入", "交付", "跑通", "设计", "定稿"):
        result = result.removeprefix(prefix)
    return result.strip(" ：，。") or text


def _strip_sentence_end(text: str) -> str:
    return str(text).strip().strip("。!！；;")


def _short_sentence(text: str, max_chars: int) -> str:
    value = " ".join(str(text).split()).strip()
    value = value[: max(1, max_chars - 1)].rstrip("，、；;")
    if not value.endswith("。"):
        value += "。"
    return value


def _pad_section(items: list[str], fallback: list[str]) -> list[str]:
    result = list(items)
    for item in fallback:
        if len(result) >= 2:
            break
        if item not in result:
            result.append(item)
    return [_short_sentence(item, 70) for item in result[:4]]


def _unique_sentences(items: list[str], *, limit: int) -> list[str]:
    return _unique_text([_short_sentence(item, 70) for item in items])[:limit]


def _unique_text(items) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_bullets(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    items: list[str] = []
    for line in text.splitlines():
        item = line.strip()
        if item.startswith("-"):
            item = item[1:].strip()
        if item:
            items.append(item)
    return items


def _render_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _infer_focus_type(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["测试", "test", "评估", "eval"]):
        return "testing"
    if any(word in lowered for word in ["文档", "周报", "报告", "规则", "docs"]):
        return "documentation"
    if any(word in lowered for word in ["页面", "前端", "展示", "复制"]):
        return "design"
    if any(word in lowered for word in ["复盘", "review"]):
        return "review"
    return "coding"


def _parse_week_id(value: Any, default_date: date) -> str:
    if value in (None, ""):
        return repo.week_id_for_date(default_date)
    week_id = str(value)
    if re.fullmatch(r"\d{4}-W\d{2}", week_id) is None:
        raise WeeklyReportValidationError("week_id 必须形如 2026-W24。")
    return week_id


def _week_bounds(week_id: str) -> tuple[date, date]:
    year_text, week_text = week_id.split("-W", 1)
    week_start = date.fromisocalendar(int(year_text), int(week_text), 1)
    week_end = date.fromisocalendar(int(year_text), int(week_text), 5)
    return week_start, week_end
