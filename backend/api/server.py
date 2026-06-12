from __future__ import annotations

import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.repositories.database import DEFAULT_DB_PATH  # noqa: E402
from backend.services.career_chat_service import (  # noqa: E402
    CareerChatGenerationError,
    CareerChatValidationError,
    decide_career_profile_suggestion,
    get_career_chat_history,
    get_career_chat_sessions,
    send_career_chat_message,
)
from backend.services.career_recommendation_service import (  # noqa: E402
    CareerRecommendationValidationError,
    adopt_career_recommendation,
)
from backend.services.checkin_service import (  # noqa: E402
    CheckinPersistenceError,
    CheckinValidationError,
    save_daily_checkin,
)
from backend.services.goal_feedback_service import (  # noqa: E402
    GoalFeedbackPersistenceError,
    GoalFeedbackValidationError,
    revise_goal_from_feedback,
)
from backend.services.history_service import (  # noqa: E402
    HistoryValidationError,
    get_history,
)
from backend.services.project_lifecycle_service import (  # noqa: E402
    get_project_overview,
)
from backend.services.runtime_maintenance_service import start_background_maintenance  # noqa: E402
from backend.services.soul_context import SOUL_PATH  # noqa: E402
from backend.services.soul_project_import_service import (  # noqa: E402
    SoulProjectImportError,
    import_current_projects_from_soul,
)
from backend.services.soul_sync_service import (  # noqa: E402
    get_soul_sync_status,
    retry_soul_sync_jobs,
)
from backend.services.today_goal_service import (  # noqa: E402
    DailyGoalGenerationError,
    get_or_generate_today_goal,
    regenerate_today_goal,
)
from backend.services.weekly_report_service import (  # noqa: E402
    WeeklyReportGenerationError,
    WeeklyReportValidationError,
    generate_weekly_report,
    regenerate_weekly_report_from_feedback,
)
from backend.services.workday_policy import is_workday, today_in_workday_timezone  # noqa: E402


NON_WORKDAY_MESSAGE = "今天是非工作日，不生成新的每日工作目标。"
WORKDAY_GOAL_MESSAGE = "今天是工作日，已读取每日工作目标。"
WORKDAY_GENERATED_GOAL_MESSAGE = "今天是工作日，已生成新的每日工作目标。"

TodayProvider = Callable[[], date]


class DayPilotServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[BaseHTTPRequestHandler],
        *,
        today_provider: TodayProvider | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        soul_path: str | Path = SOUL_PATH,
    ) -> None:
        super().__init__(server_address, request_handler)
        self.today_provider = today_provider or today_in_workday_timezone
        self.db_path = Path(db_path)
        self.soul_path = Path(soul_path)


class DayPilotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/api/today-goal":
            self._handle_today_goal()
            return

        if path == "/api/history":
            self._handle_history()
            return

        if path == "/api/projects":
            self._handle_projects()
            return

        if path == "/api/soul-sync/status":
            self._handle_soul_sync_status()
            return

        if path == "/api/career-chat/sessions":
            self._handle_career_chat_sessions()
            return

        if path == "/api/career-chat/history":
            self._handle_career_chat_history()
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/checkin":
            self._handle_checkin()
            return

        if path == "/api/today-goal/regenerate":
            self._handle_today_goal_regenerate()
            return

        if path == "/api/goal-feedback":
            self._handle_goal_feedback()
            return

        if path == "/api/weekly-report/generate":
            self._handle_weekly_report_generate()
            return

        if path == "/api/weekly-report/feedback":
            self._handle_weekly_report_feedback()
            return

        if path == "/api/projects/lifecycle":
            self._handle_project_lifecycle()
            return

        if path == "/api/soul-sync/retry":
            self._handle_soul_sync_retry()
            return

        if path == "/api/soul-sync/import-projects":
            self._handle_soul_project_import()
            return

        if path == "/api/career-chat":
            self._handle_career_chat()
            return

        if path == "/api/career-chat/profile-suggestion":
            self._handle_career_profile_suggestion()
            return

        if path == "/api/career-chat/recommendation-adoption":
            self._handle_career_recommendation_adoption()
            return

        self._send_json(404, {"error": "not_found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_today_goal(self) -> None:
        today = self.server.today_provider()
        today_text = today.isoformat()
        if not is_workday(today):
            self._send_json(
                200,
                {
                    "date": today_text,
                    "is_workday": False,
                    "message": NON_WORKDAY_MESSAGE,
                    "goal": None,
                },
            )
            return

        soul_project_import = self._import_soul_projects_for_today(today)
        try:
            result = get_or_generate_today_goal(
                self.server.db_path,
                today,
                soul_path=self.server.soul_path,
            )
        except DailyGoalGenerationError as exc:
            self._send_json(500, {"error": "goal_generation_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "date": today_text,
                "is_workday": True,
                "message": WORKDAY_GENERATED_GOAL_MESSAGE
                if result.created
                else WORKDAY_GOAL_MESSAGE,
                "created": result.created,
                "created_count": result.created_count,
                "carried_over_count": result.carried_over_count,
                "active_project_count": result.active_project_count,
                "goals": result.goals,
                "goal": result.goal,
                "soul_project_import": soul_project_import,
            },
        )

    def _handle_today_goal_regenerate(self) -> None:
        today = self.server.today_provider()
        today_text = today.isoformat()
        if not is_workday(today):
            self._send_json(
                200,
                {
                    "date": today_text,
                    "is_workday": False,
                    "message": NON_WORKDAY_MESSAGE,
                    "goal": None,
                },
            )
            return

        soul_project_import = self._import_soul_projects_for_today(today)
        try:
            result = regenerate_today_goal(
                self.server.db_path,
                today,
                soul_path=self.server.soul_path,
            )
        except DailyGoalGenerationError as exc:
            self._send_json(500, {"error": "goal_regeneration_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "date": today_text,
                "is_workday": True,
                "message": WORKDAY_GENERATED_GOAL_MESSAGE,
                "created": True,
                "created_count": result.created_count,
                "carried_over_count": result.carried_over_count,
                "active_project_count": result.active_project_count,
                "goals": result.goals,
                "goal": result.goal,
                "soul_project_import": soul_project_import,
            },
        )

    def _import_soul_projects_for_today(self, today: date) -> dict[str, Any]:
        try:
            return import_current_projects_from_soul(
                self.server.db_path,
                soul_path=self.server.soul_path,
                today=today,
            ).payload
        except SoulProjectImportError as exc:
            reason = str(exc)
            status = "skipped" if "不存在" in reason else "failed"
            return {
                "status": status,
                "source": "SOUL.md",
                "message": reason,
                "reason": reason,
            }

    def _handle_checkin(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = save_daily_checkin(
                self.server.db_path,
                payload,
                default_date=self.server.today_provider(),
                soul_path=self.server.soul_path,
            )
        except CheckinValidationError as exc:
            self._send_json(400, {"error": "invalid_checkin", "detail": str(exc)})
            return
        except CheckinPersistenceError as exc:
            self._send_json(500, {"error": "checkin_persistence_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "saved": result.saved,
                "updated": result.updated,
                "can_generate_weekly_report": result.can_generate_weekly_report,
                "checkin": result.checkin,
                "updated_difficulty": result.updated_difficulty,
                "weekly_report_refresh": result.weekly_report_refresh,
                "project_progress_update": result.project_progress_update,
                "next_goal_policy": result.next_goal_policy,
            },
        )

    def _handle_goal_feedback(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = revise_goal_from_feedback(
                self.server.db_path,
                payload,
                default_date=self.server.today_provider(),
                soul_path=self.server.soul_path,
            )
        except GoalFeedbackValidationError as exc:
            self._send_json(400, {"error": "invalid_goal_feedback", "detail": str(exc)})
            return
        except GoalFeedbackPersistenceError as exc:
            self._send_json(500, {"error": "goal_feedback_persistence_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "updated_goal": result.updated_goal,
                "feedback_message": result.feedback_message,
                "feedback_signal": result.feedback_signal,
                "memory_update": result.memory_update,
            },
        )

    def _handle_weekly_report_generate(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = generate_weekly_report(
                self.server.db_path,
                payload,
                default_date=self.server.today_provider(),
            )
        except WeeklyReportValidationError as exc:
            self._send_json(400, {"error": "invalid_weekly_report_request", "detail": str(exc)})
            return
        except WeeklyReportGenerationError as exc:
            self._send_json(500, {"error": "weekly_report_generation_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "created": result.created,
                "weekly_report": result.weekly_report,
                "report_output": result.report_output,
                "weekly_focus": result.weekly_focus,
                "weekly_report_versions": result.weekly_report_versions,
                "source_snapshot": result.source_snapshot,
                "weekly_report_memory_update": result.weekly_report_memory_update,
            },
        )

    def _handle_weekly_report_feedback(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = regenerate_weekly_report_from_feedback(
                self.server.db_path,
                payload,
                default_date=self.server.today_provider(),
                soul_path=self.server.soul_path,
            )
        except WeeklyReportValidationError as exc:
            self._send_json(400, {"error": "invalid_weekly_report_feedback", "detail": str(exc)})
            return
        except WeeklyReportGenerationError as exc:
            self._send_json(500, {"error": "weekly_report_feedback_failed", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "created": result.created,
                "weekly_report": result.weekly_report,
                "report_output": result.report_output,
                "weekly_focus": result.weekly_focus,
                "weekly_report_versions": result.weekly_report_versions,
                "source_snapshot": result.source_snapshot,
                "weekly_report_memory_update": result.weekly_report_memory_update,
            },
        )

    def _handle_history(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        raw_days = (query.get("days") or ["30"])[0]
        try:
            days = int(raw_days)
        except ValueError:
            self._send_json(400, {"error": "invalid_history_request", "detail": "days must be an integer."})
            return

        try:
            result = get_history(
                self.server.db_path,
                days=days,
                default_date=self.server.today_provider(),
            )
        except HistoryValidationError as exc:
            self._send_json(400, {"error": "invalid_history_request", "detail": str(exc)})
            return

        self._send_json(
            200,
            {
                "days": result.days,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "daily_records": result.daily_records,
                "weekly_reports": result.weekly_reports,
            },
        )

    def _handle_projects(self) -> None:
        self._send_json(200, get_project_overview(self.server.db_path))

    def _handle_soul_sync_status(self) -> None:
        self._send_json(200, get_soul_sync_status(self.server.db_path))

    def _handle_soul_sync_retry(self) -> None:
        result = retry_soul_sync_jobs(
            self.server.db_path,
            soul_path=self.server.soul_path,
        )
        self._send_json(200, result.payload)

    def _handle_soul_project_import(self) -> None:
        try:
            result = import_current_projects_from_soul(
                self.server.db_path,
                soul_path=self.server.soul_path,
                today=self.server.today_provider(),
            )
        except SoulProjectImportError as exc:
            self._send_json(400, {"error": "invalid_soul_project_import", "detail": str(exc)})
            return

        self._send_json(200, result.payload)

    def _handle_career_chat_sessions(self) -> None:
        self._send_json(200, get_career_chat_sessions(self.server.db_path))

    def _handle_career_chat_history(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        raw_session_id = (query.get("session_id") or [""])[0]
        try:
            session_id = int(raw_session_id)
        except ValueError:
            self._send_json(
                400,
                {"error": "invalid_career_chat_history", "detail": "session_id must be an integer."},
            )
            return
        try:
            self._send_json(200, get_career_chat_history(self.server.db_path, session_id))
        except CareerChatValidationError as exc:
            self._send_json(400, {"error": "invalid_career_chat_history", "detail": str(exc)})

    def _handle_career_chat(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = send_career_chat_message(
                self.server.db_path,
                payload,
                soul_path=self.server.soul_path,
                today=self.server.today_provider(),
            )
        except CareerChatValidationError as exc:
            self._send_json(400, {"error": "invalid_career_chat", "detail": str(exc)})
            return
        except CareerChatGenerationError as exc:
            self._send_json(500, {"error": "career_chat_failed", "detail": str(exc)})
            return

        self._send_json(200, result.payload)

    def _handle_career_profile_suggestion(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = decide_career_profile_suggestion(
                self.server.db_path,
                payload,
                soul_path=self.server.soul_path,
            )
        except CareerChatValidationError as exc:
            self._send_json(400, {"error": "invalid_career_profile_suggestion", "detail": str(exc)})
            return

        self._send_json(200, result.payload)

    def _handle_career_recommendation_adoption(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_json", "detail": str(exc)})
            return

        try:
            result = adopt_career_recommendation(
                self.server.db_path,
                payload,
                today=self.server.today_provider(),
                soul_path=self.server.soul_path,
            )
        except CareerRecommendationValidationError as exc:
            self._send_json(400, {"error": "invalid_career_recommendation_adoption", "detail": str(exc)})
            return

        self._send_json(200, result.payload)

    def _handle_project_lifecycle(self) -> None:
        self._send_json(
            410,
            {
                "error": "project_lifecycle_disabled",
                "detail": "项目变更入口已收敛到 SOUL.md。请编辑 SOUL.md 的当前项目段后刷新 Today。",
            },
        )

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length 必须是整数。") from exc
        if length <= 0:
            return {}

        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("请求体必须是 JSON 对象。") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象。")
        return payload

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    today_provider: TodayProvider | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    soul_path: str | Path = SOUL_PATH,
) -> DayPilotServer:
    return DayPilotServer(
        (host, port),
        DayPilotHandler,
        today_provider=today_provider,
        db_path=db_path,
        soul_path=soul_path,
    )


def main() -> None:
    host = os.environ.get("DAYPILOT_HOST", "127.0.0.1")
    port = int(os.environ.get("DAYPILOT_PORT", "8000"))
    server = create_server(host, port)
    maintenance = start_background_maintenance(db_path=server.db_path, soul_path=server.soul_path)
    print(f"DayPilot backend listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDayPilot backend stopped.")
    finally:
        maintenance.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
