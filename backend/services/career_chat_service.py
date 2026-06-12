from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.runtime_paths import default_backup_dir
from backend.config.settings import DayPilotSettings
from backend.repositories import daypilot_repository as repo
from backend.repositories.database import DEFAULT_DB_PATH, initialize_database
from backend.services.career_recommendation_service import attach_recommendation_actions
from backend.schemas.json_schema import validate_json_schema
from backend.services.llm_client import generate_json_with_fallback
from backend.services.soul_context import SOUL_PATH, load_soul_context
from backend.services.workday_policy import today_in_workday_timezone


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAREER_CHAT_SCHEMA_PATH = PROJECT_ROOT / "backend" / "schemas" / "career_chat_response.schema.json"
CAREER_BACKUP_DIR = default_backup_dir()

PROMPT_VERSION_MOCK = "career_chat_v1_mock"
PROMPT_VERSION_DEEPSEEK = "career_chat_v1_deepseek"
MOCK_MODEL_NAME = "mock-career-planning-adapter"

CAREER_PROFILE_CATEGORIES = {
    "current_skills": "当前技能点",
    "personality_and_work_style": "性格与工作方式",
    "development_intentions": "发展意愿",
    "career_values_and_constraints": "职业价值观与约束",
}
CAREER_CATEGORY_ORDER = [
    "current_skills",
    "personality_and_work_style",
    "development_intentions",
    "career_values_and_constraints",
]
CARD_PROMISE_PATTERNS = [
    re.compile(
        r"(?:下面|以下|下方|后面|最后|附上|给出|提供|列出|整理出|生成).{0,32}"
        r"(?:项目卡片|最小项目卡片|结构化项目卡片|卡片|project cards?|recommendation cards?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:两个|2个|两张|三张|3个|一个|1个).{0,24}"
        r"(?:可立即启动|最小|项目|实验).{0,24}(?:卡片|project cards?|recommendation cards?)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:择一|任选|选一个|可以|可).{0,24}加入\s*active", re.IGNORECASE),
]


@dataclass(frozen=True)
class CareerChatResult:
    payload: dict[str, Any]


@dataclass(frozen=True)
class CareerProfileSuggestionDecisionResult:
    payload: dict[str, Any]


class CareerChatValidationError(ValueError):
    """Raised when a career chat request is invalid."""


class CareerChatGenerationError(RuntimeError):
    """Raised when career chat output cannot be generated or persisted."""


class MockCareerPlanningAdapter:
    def generate(self, context: dict[str, Any]) -> dict[str, Any]:
        message = str(context.get("latest_message") or "")
        career_profile = dict((context.get("user_profile") or {}).get("career_profile") or {})
        active_projects = context.get("active_projects") or []
        known_skills = _profile_items(career_profile, "current_skills")
        intentions = _profile_items(career_profile, "development_intentions")
        constraints = _profile_items(career_profile, "career_values_and_constraints")
        inferred_skills = _extract_skill_items(message)
        inferred_intentions = _extract_development_items(message)
        project_hint = _first_project_name(active_projects)
        active_project_name = _first_active_project_name(active_projects)

        skills_text = "、".join((inferred_skills or known_skills or ["项目拆解", "AI 工具使用"])[:3])
        direction_text = "、".join((inferred_intentions or intentions or ["形成更灵活的职业能力组合"])[:2])
        constraint_text = "；".join((constraints or ["先用小项目验证，不做空泛职业口号"])[:2])

        recommendations = [
            {
                "title": "个人技能雷达与项目机会地图",
                "why_it_fits": (
                    f"你现在的问题更适合先把技能、兴趣和发展方向摊开成一张地图，再决定做哪个项目。"
                    f"已知线索包括：{skills_text}；方向是：{direction_text}。"
                ),
                "skills_to_build": ["技能盘点", "机会判断", "项目组合设计"],
                "estimated_time": "2-3 小时做出第一版，之后每周更新 20 分钟",
                "deliverable": "一份包含技能点、证据、想发展方向和候选项目评分的 Markdown 表格",
                "first_step": "列出 8-12 个已有技能或经验，并给每项补一条可证明的项目/作品证据。",
                "risks": "如果只写愿望、不写证据，后续建议会变成泛泛规划。",
                "not_now_reason": "如果你今天只剩 30 分钟，先记录 5 个技能和 2 个方向即可，不必追求完整地图。",
            },
            {
                "title": "小型职业作品集项目",
                "why_it_fits": (
                    f"DayPilot 的记录显示你偏好可交付结果。这个项目把职业规划落成作品，而不是停在分析。"
                    f"可从 {project_hint} 或当前最有成长价值的主题里切一个最小成果。"
                ),
                "skills_to_build": ["作品化表达", "需求定义", "最小可交付产品"],
                "estimated_time": "3-5 天，每天 45-90 分钟",
                "deliverable": "一个可以展示的 demo、案例文档或实验记录页面",
                "first_step": "选一个最想被未来机会看到的能力，用一句话定义作品集项目的输入、输出和验收标准。",
                "risks": "范围容易扩成大项目，需要限制在一个可演示成果上。",
                "not_now_reason": "如果当前主线项目正在收尾，先把作品集想法写进候选池，不要立刻打断主线。",
            },
        ]

        if _message_mentions_agent(message) or _profile_mentions(career_profile, ["agent", "智能体", "LLM"]):
            recommendations.insert(
                0,
                {
                    "title": "Agent 能力成长实验",
                    "why_it_fits": "你的当前项目和长期方向都靠近 Agent 系统设计，最适合用一个小实验同时提升技能和形成作品证据。",
                    "skills_to_build": ["Agent 任务拆解", "评估设计", "提示词与工具调用"],
                    "estimated_time": "4-6 小时拆成 3 个小目标",
                    "deliverable": "一个带输入样例、运行结果和失败分析的 Agent 实验记录",
                    "first_step": "挑一个真实任务，写出 Agent 成功/失败的 5 条评估标准。",
                    "risks": "不要直接做完整平台，先做一个可复现的实验。",
                    "not_now_reason": "如果缺少真实任务样例，先补样例集，再写 Agent 逻辑。",
                },
            )

        _ensure_mock_recommendation_bindings(recommendations, active_project_name)
        suggestions = _profile_suggestions_from_message(message)
        if not suggestions and not any(_profile_items(career_profile, category) for category in CAREER_CATEGORY_ORDER):
            suggestions = [
                {
                    "category": "development_intentions",
                    "items": ["希望通过项目驱动方式形成可迁移的职业能力"],
                    "evidence": "画像较空，且用户正在使用职业规划助手补全发展方向。",
                    "reason": "这是低风险的初始发展意愿，保存后可帮助后续建议更贴合。",
                }
            ]

        assistant_message = (
            "我会先把职业规划压成可验证的小项目，而不是直接给宏大路线。"
            "下面这些建议都以你的画像、当前项目和可交付偏好为依据；我会自动沉淀明确、稳定的画像线索。"
        )
        return {
            "schema_version": "career_chat_response.v1",
            "assistant_message": assistant_message,
            "recommendations": recommendations[:6],
            "profile_update_suggestions": suggestions[:6],
        }


def send_career_chat_message(
    db_path: str | Path = DEFAULT_DB_PATH,
    request_body: dict[str, Any] | None = None,
    *,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path = SOUL_PATH,
    today: date | None = None,
) -> CareerChatResult:
    request_body = request_body or {}
    message = _compact_text(request_body.get("message"), 2400, preserve_lines=True)
    if not message:
        raise CareerChatValidationError("message is required.")
    available_minutes = _optional_positive_int(request_body.get("available_minutes"), maximum=720)
    requested_session_id = _optional_positive_int(request_body.get("session_id"), maximum=10_000_000)
    today = today or today_in_workday_timezone()

    connection = initialize_database(db_path)
    try:
        with connection:
            _ensure_user_profile(connection)
            session_id = _resolve_or_create_session(connection, requested_session_id, message)
            context = _build_career_context(
                connection,
                session_id=session_id,
                latest_message=message,
                available_minutes=available_minutes,
                today=today,
                soul_path=Path(soul_path),
            )
            user_message_id = repo.create_career_chat_message(
                connection,
                session_id=session_id,
                role="user",
                content=message,
                context_snapshot=_context_snapshot(context),
            )
    finally:
        connection.close()

    try:
        llm_result = generate_json_with_fallback(
            task_name="career_chat",
            prompt_version_deepseek=PROMPT_VERSION_DEEPSEEK,
            prompt_version_mock=PROMPT_VERSION_MOCK,
            mock_model_name=MOCK_MODEL_NAME,
            build_messages=lambda soul: _career_chat_messages(context, soul),
            mock_generate=lambda: MockCareerPlanningAdapter().generate(context),
            validator=lambda output: validate_career_chat_response(
                output,
                active_project_names=_active_project_names(context),
            ),
            normalizer=normalize_career_chat_response,
            repair_hint={
                "career_chat_semantics": (
                    "If assistant_message promises project/recommendation cards, move those concrete "
                    "options into recommendations or remove the promise. Each recommendation must include "
                    "project_binding. For existing_project, copy active_projects[].name exactly. For "
                    "new_project, use a distinct project name."
                )
            },
            settings=settings,
            soul_path=soul_path,
        )
        output = normalize_career_chat_response(llm_result.output)
        validate_career_chat_response(output, active_project_names=_active_project_names(context))
    except Exception as exc:  # noqa: BLE001 - API returns concise generation failure
        raise CareerChatGenerationError(_safe_error(exc)) from exc

    connection = initialize_database(db_path)
    try:
        with connection:
            assistant_message_id = repo.create_career_chat_message(
                connection,
                session_id=session_id,
                role="assistant",
                content=output["assistant_message"],
                recommendations=output["recommendations"],
                profile_update_suggestions=output["profile_update_suggestions"],
                context_snapshot=_context_snapshot(context),
                llm_metadata=llm_result.metadata,
            )
            suggestion_records = _create_and_apply_profile_suggestions(
                connection,
                session_id=session_id,
                message_id=assistant_message_id,
                suggestions=output["profile_update_suggestions"],
            )
            assistant_message = repo.get_career_chat_message(connection, assistant_message_id)
            user_message = repo.get_career_chat_message(connection, user_message_id)
            session = repo.get_career_chat_session(connection, session_id)
            if assistant_message is not None:
                assistant_message = attach_recommendation_actions(connection, [assistant_message])[0]
    finally:
        connection.close()

    career_profile_update = _sync_auto_applied_career_profile(
        db_path,
        applied_suggestion_count=len(suggestion_records),
        soul_path=soul_path,
    )

    if assistant_message is None or user_message is None or session is None:
        raise CareerChatGenerationError("career_chat_persistence_failed")
    return CareerChatResult(
        {
            "session_id": session_id,
            "session": session,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "recommendations": assistant_message.get("recommendations") if assistant_message else output["recommendations"],
            "profile_update_suggestions": suggestion_records,
            "career_profile_update": career_profile_update,
            "llm_metadata": llm_result.metadata,
        }
    )


def get_career_chat_sessions(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    connection = initialize_database(db_path)
    try:
        return {"sessions": repo.list_career_chat_sessions(connection, limit=30)}
    finally:
        connection.close()


def _create_and_apply_profile_suggestions(
    connection,
    *,
    session_id: int,
    message_id: int,
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not suggestions:
        return []
    profile = repo.get_user_profile(connection)
    if profile is None:
        raise CareerChatGenerationError("user_profile_not_found")

    next_profile = dict(profile.get("career_profile") or {})
    suggestion_records = []
    for suggestion in suggestions:
        suggestion_id = repo.create_career_profile_update_suggestion(
            connection,
            session_id=session_id,
            message_id=message_id,
            status="applied",
            category=suggestion["category"],
            suggestion_payload=suggestion,
            applied_at=_now_text(),
        )
        next_profile = _merge_career_profile(
            next_profile,
            suggestion,
            suggestion_id=suggestion_id,
        )
        record = repo.get_career_profile_update_suggestion(connection, suggestion_id)
        if record is not None:
            suggestion_records.append(_public_suggestion(record))

    repo.update_user_profile(
        connection,
        int(profile["id"]),
        career_profile=next_profile,
    )
    return suggestion_records


def _sync_auto_applied_career_profile(
    db_path: str | Path,
    *,
    applied_suggestion_count: int,
    soul_path: str | Path,
) -> dict[str, Any]:
    if applied_suggestion_count <= 0:
        return {
            "status": "skipped",
            "applied_suggestion_count": 0,
            "soul_synced": False,
            "soul_backup_path": None,
            "soul_sync_error": None,
        }

    soul_synced = False
    soul_sync_error = None
    soul_backup_path = None
    try:
        backup_path = sync_career_profile_to_soul(db_path, soul_path=soul_path)
        soul_synced = True
        soul_backup_path = str(backup_path) if backup_path is not None else None
    except Exception as exc:  # noqa: BLE001 - DB profile remains source of truth
        soul_sync_error = _safe_error(exc)

    return {
        "status": "applied",
        "applied_suggestion_count": applied_suggestion_count,
        "soul_synced": soul_synced,
        "soul_backup_path": soul_backup_path,
        "soul_sync_error": soul_sync_error,
    }


def get_career_chat_history(db_path: str | Path, session_id: int) -> dict[str, Any]:
    if session_id <= 0:
        raise CareerChatValidationError("session_id must be positive.")
    connection = initialize_database(db_path)
    try:
        session = repo.get_career_chat_session(connection, session_id)
        if session is None:
            raise CareerChatValidationError("career chat session not found.")
        messages = repo.list_career_chat_messages(connection, session_id)
        messages = attach_recommendation_actions(connection, messages)
        suggestions = repo.list_pending_career_profile_update_suggestions(
            connection,
            session_id=session_id,
            limit=50,
        )
        return {
            "session": session,
            "messages": messages,
            "pending_profile_update_suggestions": [_public_suggestion(item) for item in suggestions],
        }
    finally:
        connection.close()


def decide_career_profile_suggestion(
    db_path: str | Path,
    request_body: dict[str, Any],
    *,
    soul_path: str | Path = SOUL_PATH,
) -> CareerProfileSuggestionDecisionResult:
    suggestion_id = _optional_positive_int(request_body.get("suggestion_id"), maximum=10_000_000)
    if suggestion_id is None:
        raise CareerChatValidationError("suggestion_id is required.")
    decision = str(request_body.get("decision") or "").strip().lower()
    if decision not in {"apply", "dismiss"}:
        raise CareerChatValidationError("decision must be apply or dismiss.")

    connection = initialize_database(db_path)
    try:
        with connection:
            _ensure_user_profile(connection)
            suggestion = repo.get_career_profile_update_suggestion(connection, suggestion_id)
            if suggestion is None:
                raise CareerChatValidationError("profile suggestion not found.")
            if suggestion["status"] != "pending":
                raise CareerChatValidationError("profile suggestion has already been decided.")
            if decision == "dismiss":
                updated = repo.update_career_profile_update_suggestion(
                    connection,
                    suggestion_id,
                    status="dismissed",
                )
                return CareerProfileSuggestionDecisionResult(
                    {
                        "status": "dismissed",
                        "suggestion": _public_suggestion(updated or suggestion),
                        "career_profile": repo.get_user_profile(connection)["career_profile"],
                        "soul_synced": False,
                    }
                )

            profile = repo.get_user_profile(connection)
            if profile is None:
                raise CareerChatValidationError("user_profile_not_found")
            payload = dict(suggestion.get("suggestion_payload") or {})
            next_profile = _merge_career_profile(
                profile.get("career_profile") or {},
                payload,
                suggestion_id=suggestion_id,
            )
            repo.update_user_profile(
                connection,
                int(profile["id"]),
                career_profile=next_profile,
            )
            updated = repo.update_career_profile_update_suggestion(
                connection,
                suggestion_id,
                status="applied",
                applied_at=_now_text(),
            )
    finally:
        connection.close()

    soul_synced = False
    soul_sync_error = None
    soul_backup_path = None
    try:
        backup_path = sync_career_profile_to_soul(db_path, soul_path=soul_path)
        soul_synced = True
        soul_backup_path = str(backup_path) if backup_path is not None else None
    except Exception as exc:  # noqa: BLE001 - DB profile remains source of truth
        soul_sync_error = _safe_error(exc)

    connection = initialize_database(db_path)
    try:
        profile = repo.get_user_profile(connection)
        suggestion_record = repo.get_career_profile_update_suggestion(connection, suggestion_id)
    finally:
        connection.close()
    return CareerProfileSuggestionDecisionResult(
        {
            "status": "applied",
            "suggestion": _public_suggestion(suggestion_record or updated),
            "career_profile": (profile or {}).get("career_profile") or {},
            "soul_synced": soul_synced,
            "soul_backup_path": soul_backup_path,
            "soul_sync_error": soul_sync_error,
        }
    )


def sync_career_profile_to_soul(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    soul_path: str | Path = SOUL_PATH,
) -> Path | None:
    connection = initialize_database(db_path)
    try:
        profile = repo.get_user_profile(connection)
        if profile is None:
            raise CareerChatValidationError("user_profile_not_found")
        return _sync_soul_career_sections(Path(soul_path), profile.get("career_profile") or {})
    finally:
        connection.close()


def validate_career_chat_response(
    output: dict[str, Any],
    *,
    active_project_names: list[str] | set[str] | tuple[str, ...] | None = None,
) -> None:
    validate_json_schema(output, _load_career_chat_schema())
    _validate_career_chat_semantics(output, active_project_names=active_project_names)


def normalize_career_chat_response(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("career_chat_output_not_object")
    recommendations = _normalize_optional_recommendations(output.get("recommendations"))
    _apply_project_bindings(recommendations, output.get("recommendations"))
    suggestions = _normalize_profile_update_suggestions(output.get("profile_update_suggestions"))
    message = _compact_text(output.get("assistant_message"), 1600, preserve_lines=True)
    if len(message) < 8:
        if recommendations:
            titles = "；".join(item["title"] for item in recommendations)
            message = f"我建议先从这些可交付成长项目里选一个：{titles}"
        else:
            message = "我会先根据你的技能、性格、发展意愿和现实约束，帮你判断下一步该补充哪些信息或推进哪些行动。"
    return {
        "schema_version": "career_chat_response.v1",
        "assistant_message": message,
        "recommendations": recommendations,
        "profile_update_suggestions": suggestions,
    }


def _validate_career_chat_semantics(
    output: dict[str, Any],
    *,
    active_project_names: list[str] | set[str] | tuple[str, ...] | None = None,
) -> None:
    recommendations = output.get("recommendations") if isinstance(output.get("recommendations"), list) else []
    message = str(output.get("assistant_message") or "")
    if not recommendations and _assistant_promises_recommendation_cards(message):
        raise ValueError("career_chat_promised_cards_without_structured_recommendations")
    if active_project_names is None:
        return
    active_names = {str(name).strip() for name in active_project_names if str(name).strip()}
    for index, recommendation in enumerate(recommendations):
        binding = recommendation.get("project_binding") if isinstance(recommendation, dict) else None
        if not isinstance(binding, dict):
            raise ValueError(f"career_recommendation_missing_project_binding:{index}")
        kind = str(binding.get("kind") or "").strip()
        project_name = str(binding.get("project_name") or "").strip()
        if kind == "existing_project" and project_name not in active_names:
            raise ValueError(f"career_recommendation_unknown_existing_project:{index}")
        if kind == "new_project" and project_name in active_names:
            raise ValueError(f"career_recommendation_new_project_collides_with_active_project:{index}")


def _assistant_promises_recommendation_cards(message: str) -> bool:
    text = " ".join(str(message or "").split())
    if not text:
        return False
    for pattern in CARD_PROMISE_PATTERNS:
        match = pattern.search(text)
        if match and not _has_nearby_card_negation(text, match.start()):
            return True
    return False


def _has_nearby_card_negation(text: str, index: int) -> bool:
    window = text[max(0, index - 3) : index + 1]
    return any(token in window for token in ("不", "不是", "无需", "不用", "不要", "暂不", "不必"))


def _load_career_chat_schema() -> dict[str, Any]:
    return json.loads(CAREER_CHAT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve_or_create_session(connection, requested_session_id: int | None, message: str) -> int:
    if requested_session_id is not None:
        session = repo.get_career_chat_session(connection, requested_session_id)
        if session is None or session["status"] != "active":
            raise CareerChatValidationError("career chat session not found.")
        return requested_session_id
    return repo.create_career_chat_session(connection, title=_session_title(message))


def _ensure_user_profile(connection) -> dict[str, Any]:
    profile = repo.get_user_profile(connection)
    if profile is not None:
        return profile
    profile_id = repo.create_user_profile(
        connection,
        id=1,
        display_name="DayPilot User",
        long_term_direction="尚未填写长期方向，职业规划助手会优先通过聊天补全。",
        current_focus_projects=[],
        goal_preferences={},
        avoid_patterns=[],
        career_profile={},
        default_available_minutes=90,
        timezone="Asia/Shanghai",
        workday_rule={"days": [1, 2, 3, 4, 5]},
    )
    profile = repo.get_user_profile(connection, profile_id)
    if profile is None:
        raise CareerChatGenerationError("default_user_profile_not_persisted")
    return profile


def _build_career_context(
    connection,
    *,
    session_id: int,
    latest_message: str,
    available_minutes: int | None,
    today: date,
    soul_path: Path,
) -> dict[str, Any]:
    profile = _ensure_user_profile(connection)
    soul = load_soul_context(soul_path)
    today_text = today.isoformat()
    return {
        "today": today_text,
        "latest_message": latest_message,
        "available_minutes": available_minutes,
        "session_id": session_id,
        "user_profile": profile,
        "active_projects": repo.list_projects(connection),
        "completed_projects": repo.list_completed_projects(connection, limit=5),
        "ability_state": repo.get_current_ability_state(connection) or {},
        "recent_daily_goals": repo.list_recent_daily_goal_records(connection, today_text, limit=6),
        "recent_checkins": repo.list_recent_daily_checkins(connection, today_text, limit=6),
        "recent_feedback_messages": repo.list_recent_feedback_messages(connection, today_text, limit=8),
        "recent_weekly_focus": repo.list_recent_weekly_focus(connection, limit=5),
        "chat_history": repo.list_recent_career_chat_messages(connection, session_id, limit=10),
        "soul_loaded": soul.loaded,
        "soul_path": soul.path,
        "soul_excerpt": soul.content[:6000],
    }


def _career_chat_messages(context: dict[str, Any], soul: str) -> list[dict[str, str]]:
    system = f"""{soul}

You are DayPilot's private career development planning assistant.
Return exactly one valid JSON object matching career_chat_response.v1.
You help the single local user decide how to use spare time for career growth based on personality, skills, development intentions, constraints, and project history.
Assistant text may provide clarifying questions, direction analysis, risk warnings, next steps, or project ideas.
Keep assistant_message readable and brief: use 2 to 4 short Chinese paragraphs, no Markdown heading markers, and no long numbered project-card dump.
Put concrete project or experiment options in recommendations instead of repeating full project cards inside assistant_message.
If assistant_message says there are project cards/options below, recommendations must contain those cards.
Do not create, update, or claim to create DayPilot projects, daily goals, check-ins, or weekly reports.
Do not browse the web or invent market data. Make advice from the provided local context only.
Structured recommendations are optional. When you include them, each must be a concrete project or experiment with a visible deliverable.
Each recommendation must include project_binding.
For project_binding.kind="existing_project", project_binding.project_name must exactly copy one active_projects[].name.
For project_binding.kind="new_project", project_binding.project_name must be the new project name you want DayPilot to create or restore.
Keep the recommendation title focused on the experiment or task; use project_binding for project ownership.
If you infer stable user-profile facts with clear evidence, put them in profile_update_suggestions because DayPilot will save them automatically.
Do not include temporary moods, one-off constraints, guesses, or sensitive conclusions in profile_update_suggestions.
Use concise Chinese for user-facing text.
"""
    user = {
        "task": "Help with private career-development planning and identify optional profile updates.",
        "required_schema": "career_chat_response.v1",
        "latest_user_message": context["latest_message"],
        "available_minutes": context["available_minutes"],
        "today": context["today"],
        "career_profile": (context["user_profile"] or {}).get("career_profile") or {},
        "long_term_direction": (context["user_profile"] or {}).get("long_term_direction") or "",
        "goal_preferences": (context["user_profile"] or {}).get("goal_preferences") or {},
        "avoid_patterns": (context["user_profile"] or {}).get("avoid_patterns") or [],
        "active_projects": context["active_projects"],
        "completed_projects": context["completed_projects"],
        "ability_state": context["ability_state"],
        "recent_daily_goals": context["recent_daily_goals"],
        "recent_checkins": context["recent_checkins"],
        "recent_feedback_messages": context["recent_feedback_messages"],
        "recent_weekly_focus": context["recent_weekly_focus"],
        "chat_history": [
            {
                "role": item["role"],
                "content": item["content"],
                "recommendations": item.get("recommendations") or [],
            }
            for item in context["chat_history"]
        ],
        "rules": [
            "recommendations may be an empty array when assistant_message is the better response.",
            "assistant_message should summarize direction and next-step reasoning; detailed project choices belong in recommendations.",
            "If assistant_message promises project cards or says the user can choose one to join active, recommendations must be non-empty.",
            "Use structured recommendations only when concrete project cards help the user decide what to do next.",
            "Each structured recommendation must name a deliverable.",
            "Each structured recommendation must include project_binding.",
            "Use project_binding.kind='existing_project' only when project_binding.project_name exactly matches active_projects[].name.",
            "Use project_binding.kind='new_project' when this is not a direct extension of an active project; project_name is the new project name.",
            "profile_update_suggestions are saved automatically, so include only stable and evidence-backed profile facts.",
            "Do not output Markdown fences.",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def _context_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "today": context["today"],
        "available_minutes": context.get("available_minutes"),
        "profile_id": (context.get("user_profile") or {}).get("id"),
        "career_profile_keys": sorted(((context.get("user_profile") or {}).get("career_profile") or {}).keys()),
        "active_project_ids": [item["id"] for item in context.get("active_projects") or []],
        "recent_daily_goal_ids": [item["daily_goal"]["id"] for item in context.get("recent_daily_goals") or []],
        "recent_checkin_ids": [item["id"] for item in context.get("recent_checkins") or []],
        "recent_feedback_message_ids": [item["id"] for item in context.get("recent_feedback_messages") or []],
        "recent_weekly_focus_ids": [item["id"] for item in context.get("recent_weekly_focus") or []],
        "chat_history_message_ids": [item["id"] for item in context.get("chat_history") or []],
        "soul_loaded": context.get("soul_loaded"),
        "soul_path": context.get("soul_path"),
    }


def _active_project_names(context: dict[str, Any]) -> list[str]:
    return [
        str(project.get("name") or "").strip()
        for project in context.get("active_projects") or []
        if str(project.get("status") or "active") == "active" and str(project.get("name") or "").strip()
    ]


def _normalize_optional_recommendations(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    recommendations = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = _compact_text(item.get("title"), 90)
        if len(title) < 4:
            continue
        recommendations.append(
            {
                "title": title,
                "why_it_fits": _with_fallback(item.get("why_it_fits"), "这个建议能把当前画像线索转成可验证的成长结果。", 260),
                "skills_to_build": _string_list(item.get("skills_to_build"), ["项目拆解"], max_items=6, max_chars=60),
                "estimated_time": _with_fallback(item.get("estimated_time"), "45-90 分钟完成第一步", 80),
                "deliverable": _with_fallback(item.get("deliverable"), "一份可检查的项目记录或实验结果", 160),
                "first_step": _with_fallback(item.get("first_step"), "先写清输入、输出和验收标准。", 180),
                "risks": _with_fallback(item.get("risks"), "主要风险是范围过大，需要保持最小可交付。", 220),
                "not_now_reason": _with_fallback(item.get("not_now_reason"), "如果今天时间不足，先记录候选，不急着展开。", 220),
            }
        )
    return recommendations[:6]


def _apply_project_bindings(recommendations: list[dict[str, Any]], raw_value: Any) -> None:
    raw_items = raw_value if isinstance(raw_value, list) else []
    valid_raw_items = [
        item
        for item in raw_items
        if isinstance(item, dict) and len(_compact_text(item.get("title"), 90)) >= 4
    ]
    for recommendation, raw_item in zip(recommendations, valid_raw_items):
        project_binding = _normalize_project_binding(raw_item.get("project_binding"))
        if project_binding is not None:
            recommendation["project_binding"] = project_binding


def _normalize_project_binding(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    project_name = _compact_text(value.get("project_name"), 90)
    if kind not in {"existing_project", "new_project"} or len(project_name) < 2:
        return None
    binding = {
        "kind": kind,
        "project_name": project_name,
    }
    reason = _compact_text(value.get("reason"), 180)
    if reason:
        binding["reason"] = reason
    return binding


def _normalize_recommendations(value: Any) -> list[dict[str, Any]]:
    return _normalize_optional_recommendations(value)
    raw_items = value if isinstance(value, list) else []
    recommendations = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = _compact_text(item.get("title"), 90)
        if len(title) < 4:
            continue
        recommendations.append(
            {
                "title": title,
                "why_it_fits": _with_fallback(item.get("why_it_fits"), "该项目能把当前画像线索转成可验证的成长成果。", 260),
                "skills_to_build": _string_list(item.get("skills_to_build"), ["项目拆解"], max_items=6, max_chars=60),
                "estimated_time": _with_fallback(item.get("estimated_time"), "45-90 分钟完成第一步", 80),
                "deliverable": _with_fallback(item.get("deliverable"), "一份可检查的项目记录或实验结果", 160),
                "first_step": _with_fallback(item.get("first_step"), "先写清楚输入、输出和验收标准。", 180),
                "risks": _with_fallback(item.get("risks"), "主要风险是范围过大，需要保持最小可交付。", 220),
                "not_now_reason": _with_fallback(item.get("not_now_reason"), "如果今天时间不足，先记录候选，不急着展开。", 220),
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "title": "补全个人职业画像小档案",
                "why_it_fits": "当前画像信息不足，先补全技能、性格、发展意愿和约束，后续建议才不会泛化。",
                "skills_to_build": ["自我盘点", "职业叙事", "项目选择"],
                "estimated_time": "30-45 分钟",
                "deliverable": "一份包含技能、性格、发展意愿和职业约束的 Markdown 小档案",
                "first_step": "写下 5 个当前技能点，并给每个技能补一条证据。",
                "risks": "不要把画像写成愿望清单，每条尽量带证据。",
                "not_now_reason": "如果你已经有紧急交付，先补 3 条最关键技能即可。",
            }
        )
    return recommendations[:6]


def _normalize_profile_update_suggestions(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    suggestions = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if category not in CAREER_PROFILE_CATEGORIES:
            continue
        items = _string_list(item.get("items"), [], max_items=8, max_chars=120)
        if not items:
            continue
        key = f"{category}:{'|'.join(_normalize_key(text) for text in items)}"
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(
            {
                "category": category,
                "items": items,
                "evidence": _with_fallback(item.get("evidence"), "来自本轮职业规划聊天。", 260),
                "reason": _with_fallback(item.get("reason"), "保存后可让后续职业建议更贴合。", 260),
            }
        )
    return suggestions[:6]


def _profile_suggestions_from_message(message: str) -> list[dict[str, Any]]:
    suggestions = []
    skills = _extract_skill_items(message)
    if skills:
        suggestions.append(
            {
                "category": "current_skills",
                "items": skills,
                "evidence": message[:220],
                "reason": "用户在聊天中直接提到了当前技能或经验。",
            }
        )
    development = _extract_development_items(message)
    if development:
        suggestions.append(
            {
                "category": "development_intentions",
                "items": development,
                "evidence": message[:220],
                "reason": "用户表达了希望发展的方向，可用于后续项目建议排序。",
            }
        )
    work_style = _extract_work_style_items(message)
    if work_style:
        suggestions.append(
            {
                "category": "personality_and_work_style",
                "items": work_style,
                "evidence": message[:220],
                "reason": "用户表达了稳定的工作方式或性格偏好，可用于后续职业建议。",
            }
        )
    constraints = _extract_constraint_items(message)
    if constraints:
        suggestions.append(
            {
                "category": "career_values_and_constraints",
                "items": constraints,
                "evidence": message[:220],
                "reason": "用户表达了职业选择约束或价值偏好。",
            }
        )
    return suggestions


def _extract_skill_items(message: str) -> list[str]:
    patterns = [
        (r"会\s*([A-Za-z0-9+#.\u4e00-\u9fff /-]{2,30})", "会{}"),
        (r"擅长\s*([A-Za-z0-9+#.\u4e00-\u9fff /-]{2,30})", "擅长{}"),
        (r"技能(?:是|有|包括)?\s*([A-Za-z0-9+#.\u4e00-\u9fff 、,，/-]{2,80})", "{}"),
    ]
    items: list[str] = []
    for pattern, template in patterns:
        for match in re.findall(pattern, message):
            for part in _split_phrase(match):
                items.append(template.format(part).strip())
    keyword_map = {
        "Python": "Python",
        "机器学习": "机器学习",
        "深度学习": "深度学习",
        "Agent": "AI Agent",
        "智能体": "AI Agent",
        "LLM": "LLM 应用",
        "前端": "前端开发",
        "后端": "后端开发",
        "规则": "规则系统设计",
        "SFT": "SFT 训练",
        "RL": "RL 训练",
    }
    for token, label in keyword_map.items():
        if token.lower() in message.lower():
            items.append(label)
    return _dedupe(items)[:8]


def _extract_development_items(message: str) -> list[str]:
    items = []
    if any(token in message for token in ["想转", "转型", "未来", "发展", "职业", "方向"]):
        for token in ["AI Agent", "智能体", "机器学习", "产品", "架构", "独立开发", "自由职业", "研究", "算法"]:
            if token.lower() in message.lower():
                items.append(f"希望发展到{token}方向")
        if not items:
            items.append("希望明确未来职业发展方向")
    return _dedupe(items)[:6]


def _extract_work_style_items(message: str) -> list[str]:
    items = []
    mapping = {
        "内向": "偏内向，适合深度思考和异步表达",
        "外向": "偏外向，适合高频沟通和公开表达",
        "拖延": "容易拖延，需要更小的第一步和明确反馈",
        "焦虑": "容易焦虑，需要降低范围和保留缓冲",
        "喜欢项目": "偏好项目驱动学习",
        "项目驱动": "偏好项目驱动学习",
        "不喜欢纯学习": "不喜欢纯学习，偏好带交付物的成长任务",
    }
    for token, item in mapping.items():
        if token in message:
            items.append(item)
    return _dedupe(items)[:6]


def _extract_constraint_items(message: str) -> list[str]:
    items = []
    if any(token in message for token in ["不想", "不要", "不能", "时间少", "碎片", "下班后"]):
        if "时间" in message or "碎片" in message or "下班后" in message:
            items.append("职业发展项目需要兼容碎片时间和有限精力")
        if "不想" in message or "不要" in message:
            items.append("职业建议需要避开用户明确不想投入的方向")
    return _dedupe(items)[:6]


def _merge_career_profile(
    current_profile: dict[str, Any],
    suggestion: dict[str, Any],
    *,
    suggestion_id: int,
) -> dict[str, Any]:
    profile = dict(current_profile or {})
    category = str(suggestion.get("category") or "").strip()
    if category not in CAREER_PROFILE_CATEGORIES:
        raise CareerChatValidationError("invalid profile suggestion category.")
    existing_items = _string_list(profile.get(category), [], max_items=200, max_chars=180)
    merged = _merge_unique(existing_items, _string_list(suggestion.get("items"), [], max_items=20, max_chars=180))
    profile[category] = merged
    evidence = list(profile.get("evidence") or []) if isinstance(profile.get("evidence"), list) else []
    evidence.append(
        {
            "category": category,
            "items": suggestion.get("items") or [],
            "evidence": suggestion.get("evidence") or "",
            "reason": suggestion.get("reason") or "",
            "source": "career_chat",
            "suggestion_id": suggestion_id,
            "updated_at": _now_text(),
        }
    )
    profile["evidence"] = evidence[-50:]
    profile["updated_from_career_chat_at"] = _now_text()
    return profile


def _sync_soul_career_sections(soul_path: Path, career_profile: dict[str, Any]) -> Path | None:
    if soul_path.exists():
        text = soul_path.read_text(encoding="utf-8")
        backup_path = _backup_soul(soul_path)
    else:
        text = "# DayPilot SOUL\n"
        backup_path = None

    cleaned = _remove_career_sections(text)
    block = _render_career_sections(career_profile)
    insert_marker = "\n## 当前项目"
    insert_at = cleaned.find(insert_marker)
    if insert_at >= 0:
        next_text = cleaned[:insert_at].rstrip() + "\n\n" + block + "\n" + cleaned[insert_at:]
    else:
        next_text = cleaned.rstrip() + "\n\n" + block + "\n"
    soul_path.write_text(next_text, encoding="utf-8")
    return backup_path


def _remove_career_sections(text: str) -> str:
    markers = "|".join(re.escape(label) for label in CAREER_PROFILE_CATEGORIES.values())
    pattern = rf"\n?## (?:{markers})\n.*?(?=\n## |\Z)"
    return re.sub(pattern, "", text, flags=re.DOTALL).strip() + "\n"


def _render_career_sections(career_profile: dict[str, Any]) -> str:
    sections = []
    for category in CAREER_CATEGORY_ORDER:
        title = CAREER_PROFILE_CATEGORIES[category]
        items = _string_list(career_profile.get(category), [], max_items=200, max_chars=180)
        lines = [f"## {title}", ""]
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- 暂未确认。")
        sections.append("\n".join(lines).rstrip())
    return "\n\n".join(sections).rstrip()


def _backup_soul(soul_path: Path) -> Path:
    CAREER_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = CAREER_BACKUP_DIR / f"SOUL_{stamp}.md"
    shutil.copy2(soul_path, backup_path)
    return backup_path


def _public_suggestion(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {}
    payload = dict(record.get("suggestion_payload") or {})
    return {
        "id": record["id"],
        "session_id": record["session_id"],
        "message_id": record["message_id"],
        "status": record["status"],
        "category": record["category"],
        "items": payload.get("items") or [],
        "evidence": payload.get("evidence") or "",
        "reason": payload.get("reason") or "",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "applied_at": record.get("applied_at"),
    }


def _profile_items(career_profile: dict[str, Any], category: str) -> list[str]:
    return _string_list(career_profile.get(category), [], max_items=50, max_chars=180)


def _profile_mentions(career_profile: dict[str, Any], tokens: list[str]) -> bool:
    text = json.dumps(career_profile or {}, ensure_ascii=False).lower()
    return any(token.lower() in text for token in tokens)


def _message_mentions_agent(message: str) -> bool:
    return any(token.lower() in message.lower() for token in ["agent", "智能体", "llm", "openai", "deepseek"])


def _first_project_name(projects: list[dict[str, Any]]) -> str:
    for project in projects:
        name = str(project.get("name") or "").strip()
        if name:
            return f"「{name}」"
    return "一个最贴近长期方向的候选项目"


def _first_active_project_name(projects: list[dict[str, Any]]) -> str:
    for project in projects:
        if str(project.get("status") or "active") != "active":
            continue
        name = str(project.get("name") or "").strip()
        if name:
            return name
    return ""


def _ensure_mock_recommendation_bindings(recommendations: list[dict[str, Any]], active_project_name: str) -> None:
    for recommendation in recommendations:
        if isinstance(recommendation.get("project_binding"), dict):
            continue
        title = _compact_text(recommendation.get("title"), 90)
        recommendation["project_binding"] = {
            "kind": "existing_project" if active_project_name else "new_project",
            "project_name": active_project_name or title or "Career Growth Project",
            "reason": (
                "Mock adapter binds the recommendation to the current active project when one exists; "
                "otherwise it names a new project from the recommendation title."
            ),
        }


def _session_title(message: str) -> str:
    text = re.sub(r"\s+", " ", message).strip()
    return text[:32] or "职业规划会话"


def _optional_positive_int(value: Any, *, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CareerChatValidationError("numeric field must be an integer.") from exc
    if number <= 0 or number > maximum:
        raise CareerChatValidationError("numeric field is out of range.")
    return number


def _string_list(value: Any, fallback: list[str], *, max_items: int, max_chars: int) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str) and value.strip():
        raw_items = _split_phrase(value)
    else:
        raw_items = fallback
    return _dedupe(_compact_text(item, max_chars) for item in raw_items if _compact_text(item, max_chars))[:max_items]


def _split_phrase(value: str) -> list[str]:
    return [
        part.strip(" ，,、;；。.")
        for part in re.split(r"[，,、;；。.\n]+", str(value or ""))
        if part.strip(" ，,、;；。.")
    ]


def _with_fallback(value: Any, fallback: str, max_chars: int) -> str:
    text = _compact_text(value, max_chars)
    return text if len(text) >= 2 else fallback[:max_chars]


def _compact_text(value: Any, max_chars: int = 2400, *, preserve_lines: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not preserve_lines:
        return " ".join(text.split()).strip()[:max_chars]

    paragraphs: list[str] = []
    for block in re.split(r"\n{2,}", text):
        lines = [" ".join(line.split()) for line in block.split("\n")]
        paragraph = "\n".join(line for line in lines if line).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return _trim_text_at_boundary("\n\n".join(paragraphs), max_chars)


def _trim_text_at_boundary(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars].rstrip()
    minimum_cut = max(8, int(max_chars * 0.65))
    for separator in ("\n\n", "\n", "。", "！", "？", ".", "；", ";", "，", ","):
        index = truncated.rfind(separator)
        if index >= minimum_cut:
            end = index if separator.startswith("\n") else index + len(separator)
            return truncated[:end].rstrip()
    return truncated


def _merge_unique(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    seen = {_normalize_key(item) for item in merged}
    for item in additions:
        key = _normalize_key(item)
        if key and key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _compact_text(value, 180)
        key = _normalize_key(text)
        if text and key and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s,，、。.;；:：'\"`]+", "", str(value or "").lower())


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:300] or exc.__class__.__name__
