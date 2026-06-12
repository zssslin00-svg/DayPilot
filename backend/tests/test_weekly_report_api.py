from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DAYPILOT_LLM_MODE"] = "mock"

from backend.api.server import create_server  # noqa: E402
from backend.repositories import daypilot_repository as repo  # noqa: E402
from backend.repositories.database import connect_database, initialize_database  # noqa: E402
from backend.services import weekly_report_service as weekly_service  # noqa: E402
from backend.services.weekly_report_resources import validate_weekly_report_output  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeDeepSeekSequence:
    def __init__(self, *contents: dict[str, Any]) -> None:
        self.contents = list(contents)
        self.calls = 0
        self.requests: list[Any] = []

    def __call__(self, request: Any, *args: object, **kwargs: object) -> FakeResponse:
        self.calls += 1
        self.requests.append(request)
        if not self.contents:
            raise AssertionError("No fake DeepSeek response left")
        return FakeResponse(self.contents.pop(0))


def _deepseek_payload(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"fake-weekly-{len(json.dumps(output, ensure_ascii=False))}",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _restore_env(values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _soul_file(root: Path) -> Path:
    soul_path = root / "SOUL.md"
    soul_path.write_text(
        "\n".join(
            [
                "# DayPilot SOUL",
                "",
                "## 当前项目",
                "",
                "1. DayPilot MVP：当前进度：准备周报测试。项目今日目标：生成周报。",
                "",
                "## 用户偏好",
                "",
                "- 小而可交付。",
                "",
                "## 周报原则",
                "",
                "- 周报只能总结有证据的完成结果。",
                "",
                "## 输出纪律",
                "",
                "- 只输出需要的内容。",
            ]
        ),
        encoding="utf-8",
    )
    return soul_path


def _post_weekly_report(
    today: date,
    db_path: Path,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    port = _free_port()
    soul_path = db_path.parent / "SOUL.md"
    if not soul_path.exists():
        _soul_file(db_path.parent)
    server = create_server(
        "127.0.0.1",
        port,
        today_provider=lambda: today,
        db_path=db_path,
        soul_path=soul_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/weekly-report/generate",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        try:
            with opener.open(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return int(response.status), payload
        except urllib.error.HTTPError as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_weekly_report_generates_persists_snapshot_and_focus() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-report.sqlite3"
        seeded = _seed_workweek(db_path)

        status, payload = _post_weekly_report(
            date(2026, 6, 12),
            db_path,
            {"week_id": "2026-W24"},
        )

        assert status == 200
        assert payload["created"] is True
        report_output = payload["report_output"]
        validate_weekly_report_output(report_output)
        assert set(report_output) == {"completed_work", "next_week_plan", "weekly_reflection"}
        assert len(report_output["completed_work"]) >= 2
        assert len(report_output["next_week_plan"]) >= 2
        assert all("周一" not in item and "周五" not in item for item in report_output["completed_work"])
        assert any("目标生成服务" in item for item in report_output["completed_work"])
        assert any("闭环" in item or "结果" in item for item in report_output["next_week_plan"])

        source_snapshot = payload["source_snapshot"]
        assert source_snapshot["daily_goal_ids"] == seeded["daily_goal_ids"]
        assert source_snapshot["active_version_ids"] == seeded["active_version_ids"]
        assert source_snapshot["checkin_ids"] == seeded["checkin_ids"]
        assert source_snapshot["feedback_message_ids"] == seeded["feedback_message_ids"]
        assert source_snapshot["ability_state_id"] == seeded["ability_state_id"]
        assert source_snapshot["friday_checkin_submitted"] is True
        assert source_snapshot["quality_review"]["passed"] is True
        assert source_snapshot["quality_review"]["quality_score"] == 5
        assert set(source_snapshot["weekly_report_preferences"]) == {
            "style_preferences",
            "avoid_patterns",
            "structure_preferences",
            "evidence_preferences",
            "revision_patterns",
        }
        assert source_snapshot["llm_metadata"]["llm_mode_used"] == "mock"

        connection = connect_database(db_path)
        try:
            weekly_report = repo.get_weekly_report_by_week(connection, "2026-W24")
            assert weekly_report is not None
            assert weekly_report["source_snapshot"]["daily_goal_ids"] == seeded["daily_goal_ids"]
            assert weekly_report["quality_score"] == 5
            assert weekly_report["report_text"].startswith("本周完成工作")
            assert weekly_report["model_name"] == "mock-weekly-report-adapter"
            weekly_focus = repo.list_weekly_focus_for_report(connection, weekly_report["id"])
            assert len(weekly_focus) >= 2
            assert weekly_focus[0]["target_week_id"] == "2026-W25"
            assert weekly_focus[0]["context_payload"]["source"] == ["weekly_report.next_week_plan"]
        finally:
            connection.close()

        second_status, second_payload = _post_weekly_report(
            date(2026, 6, 12),
            db_path,
            {"week_id": "2026-W24"},
        )
        assert second_status == 200
        assert second_payload["created"] is False
        assert second_payload["weekly_report"]["status"] == "regenerated"


def test_schema_valid_vague_report_fails_quality_review() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-report-quality-gate.sqlite3"
        _seed_workweek(db_path)
        connection = initialize_database(db_path)
        try:
            snapshot = weekly_service.build_weekly_snapshot(
                connection,
                "2026-W24",
                generated_on=date(2026, 6, 12),
            )
        finally:
            connection.close()

        vague_report = {
            "completed_work": ["完成了很多相关工作", "继续完善相关工作内容"],
            "next_week_plan": ["继续优化整体能力表现", "推进各项任务持续开展"],
            "weekly_reflection": ["总体表现不错需要保持", "下周继续开发相关内容"],
        }
        review = weekly_service.review_weekly_report(vague_report, snapshot)

        assert review["passed"] is False
        assert review["quality_score"] == 2
        assert any("空泛" in failure or "证据" in failure for failure in review["failures"])


def test_deepseek_weekly_report_schema_overflow_uses_semantic_repair() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-report-semantic-repair.sqlite3"
        _seed_workweek(db_path)
        sequence = FakeDeepSeekSequence(
            _deepseek_payload(
                {
                    "completed_work": [
                        "完成目标生成服务并留下可复查记录。",
                        "完成 check-in 保存接口并留下可复查记录。",
                        "完成后端周报聚合接口并留下可复查记录。",
                        "完成在线反馈修正链路并记录验收结果。",
                        "完成 Goal Critic 质量门的最小产出。",
                        "完成周报生成规则和前端按钮联调。",
                        "完成周报 eval 样例并验证基础结果。",
                    ],
                    "next_week_plan": [
                        "交付下周重点承接的最小可验证闭环。",
                        "补齐周报质量审查回归样例。",
                        "验证 weekly_focus 选中与回填结果。",
                        "记录前端周报按钮的验收结果。",
                        "生成周报修复路径的回归记录。",
                    ],
                    "weekly_reflection": [
                        "本周多次通过反馈收敛范围，下周需要更早切出最低版本。",
                        "高难度目标集中在质量门和周报链路，下周应减少并行范围。",
                    ],
                }
            ),
            _deepseek_payload(
                {
                    "completed_work": [
                        "完成目标生成、check-in 保存和周报聚合接口，留下可复查记录。",
                        "完成在线反馈修正链路与 Goal Critic 质量门的最小产出。",
                        "完成周报生成规则和前端按钮联调，形成三段式周报输出。",
                    ],
                    "next_week_plan": [
                        "交付下周重点承接的最小可验证闭环，记录 weekly_focus 选中与回填结果。",
                        "补齐周报质量审查回归样例，验证空话、流水账和虚构成果拦截。",
                    ],
                    "weekly_reflection": [
                        "本周多次通过反馈收敛范围，下周需要更早切出最低版本。",
                        "高难度目标集中在质量门和周报链路，下周应减少并行范围。",
                    ],
                }
            ),
        )
        original_urlopen = urllib.request.urlopen
        old_env = {
            "DAYPILOT_LLM_MODE": os.environ.get("DAYPILOT_LLM_MODE"),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
            "DAYPILOT_PREFER_DOTENV": os.environ.get("DAYPILOT_PREFER_DOTENV"),
        }
        try:
            urllib.request.urlopen = sequence  # type: ignore[assignment]
            os.environ["DAYPILOT_LLM_MODE"] = "deepseek"
            os.environ["DEEPSEEK_API_KEY"] = "fake-key"
            os.environ["DAYPILOT_PREFER_DOTENV"] = "0"
            result = weekly_service.generate_weekly_report(
                db_path,
                {"week_id": "2026-W24"},
                default_date=date(2026, 6, 12),
            )
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
            _restore_env(old_env)

        assert sequence.calls == 2
        assert result.weekly_report["model_name"] == "deepseek-v4-pro"
        assert result.source_snapshot["llm_metadata"]["schema_repair_triggered"] is True
        assert result.source_snapshot["llm_metadata"]["schema_repair_succeeded"] is True
        assert result.source_snapshot["llm_metadata"]["final_used_fallback"] is False
        assert result.source_snapshot["quality_review"]["passed"] is True
        assert len(result.report_output["completed_work"]) == 3

        repair_payload = json.loads(sequence.requests[1].data.decode("utf-8"))
        repair_user = json.loads(repair_payload["messages"][1]["content"])
        assert repair_user["repair_hint"]["repair_mode"] == "semantic_compression"


def test_deepseek_weekly_report_repair_failure_falls_back_to_mock_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-report-repair-fallback.sqlite3"
        _seed_workweek(db_path)
        too_many = {
            "completed_work": [f"完成目标生成服务相关产出 {index}，留下可复查记录。" for index in range(7)],
            "next_week_plan": ["交付下周重点承接的最小可验证闭环。", "补齐周报质量审查回归样例。"],
            "weekly_reflection": [
                "本周多次通过反馈收敛范围，下周需要更早切出最低版本。",
                "高难度目标集中在质量门和周报链路，下周应减少并行范围。",
            ],
        }
        still_too_long = {
            "completed_work": [
                (
                    "完成目标生成、check-in 保存、后端周报聚合、在线反馈修正、"
                    "Goal Critic 质量门、前端按钮联调和周报 eval 样例等所有相关工作，"
                    "并留下完整可复查记录。"
                )
            ],
            "next_week_plan": ["交付下周重点承接的最小可验证闭环。", "补齐周报质量审查回归样例。"],
            "weekly_reflection": [
                "本周多次通过反馈收敛范围，下周需要更早切出最低版本。",
                "高难度目标集中在质量门和周报链路，下周应减少并行范围。",
            ],
        }
        sequence = FakeDeepSeekSequence(_deepseek_payload(too_many), _deepseek_payload(still_too_long))
        original_urlopen = urllib.request.urlopen
        old_env = {
            "DAYPILOT_LLM_MODE": os.environ.get("DAYPILOT_LLM_MODE"),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
            "DAYPILOT_PREFER_DOTENV": os.environ.get("DAYPILOT_PREFER_DOTENV"),
        }
        try:
            urllib.request.urlopen = sequence  # type: ignore[assignment]
            os.environ["DAYPILOT_LLM_MODE"] = "deepseek"
            os.environ["DEEPSEEK_API_KEY"] = "fake-key"
            os.environ["DAYPILOT_PREFER_DOTENV"] = "0"
            result = weekly_service.generate_weekly_report(
                db_path,
                {"week_id": "2026-W24"},
                default_date=date(2026, 6, 12),
            )
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
            _restore_env(old_env)

        assert sequence.calls == 2
        metadata = result.source_snapshot["llm_metadata"]
        assert result.weekly_report["model_name"] == "mock-weekly-report-adapter"
        assert metadata["schema_repair_triggered"] is True
        assert metadata["schema_repair_succeeded"] is False
        assert metadata["final_used_fallback"] is True
        assert metadata["repair_schema_failure_reason"]


def test_weekly_report_rejects_before_friday_checkin() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "weekly-report-not-ready.sqlite3"
        _seed_workweek(db_path, include_friday_checkin=False)

        status, payload = _post_weekly_report(
            date(2026, 6, 11),
            db_path,
            {"week_id": "2026-W24"},
        )

        assert status == 400
        assert payload["error"] == "invalid_weekly_report_request"
        assert "周五 check-in" in payload["detail"]


def _seed_workweek(db_path: Path, *, include_friday_checkin: bool = True) -> dict[str, Any]:
    connection = initialize_database(db_path)
    daily_goal_ids: list[int] = []
    active_version_ids: list[int] = []
    checkin_ids: list[int] = []
    feedback_message_ids: list[int] = []
    try:
        with connection:
            repo.create_user_profile(
                connection,
                id=1,
                long_term_direction="Build a useful daily goal and weekly report loop.",
                current_focus_projects=["DayPilot MVP", "Weekly Report Generator"],
                default_available_minutes=90,
            )

            rows = [
                ("2026-06-08", "目标生成服务", 0.9, 3, [], "继续后端周报聚合接口"),
                ("2026-06-09", "check-in 保存接口", 0.8, 3, [], "接入周报 schema 校验"),
                ("2026-06-10", "在线反馈修正链路", 0.7, 4, ["质量审查失败重试"], "实现前端周报按钮"),
                ("2026-06-11", "Goal Critic 质量门", 0.6, 5, ["周末查看上一工作周周报"], "补周报生成规则"),
                ("2026-06-12", "周报生成规则", 0.75, 4, ["10 条周报评估样例"], "下周补 weekly_focus 承接"),
            ]

            for index, (goal_date, item, rate, felt, unfinished, direction) in enumerate(rows, start=1):
                daily_goal_id = repo.create_daily_goal(
                    connection,
                    goal_date=goal_date,
                    context_snapshot={"source": "weekly-report-test"},
                    generated_at=f"{goal_date} 09:00:00",
                )
                version_id = repo.create_goal_version(
                    connection,
                    daily_goal_id=daily_goal_id,
                    version_no=1,
                    is_active=1,
                    main_goal=f"完成 DayPilot {item} 的可验收产出",
                    goal_reason=f"{item} 是本周周报闭环的一部分。",
                    success_criteria=[f"交付 {item}", "记录验收结果"],
                    estimated_minutes=70 + index * 5,
                    difficulty_level=min(5, 2 + index // 2),
                    minimum_version=f"{item} 有可检查记录。",
                    stretch_challenge="补充一条回归测试。",
                    avoid_today=json.dumps(["不要扩展外部集成"], ensure_ascii=False),
                    goal_type="coding",
                    revision_source="initial_generation",
                )
                if index == 4:
                    feedback_id = repo.create_feedback_message(
                        connection,
                        daily_goal_id=daily_goal_id,
                        before_version_id=version_id,
                        raw_message="目标太大，复制和重新生成先别做。",
                        feedback_type="quality_issue",
                        affected_scope="today",
                        interpretation_json={"summary": "用户要求缩小前端周报范围。"},
                        extracted_constraints={"scope": "smaller"},
                        extracted_preferences={},
                        memory_action="none",
                        should_regenerate_goal=1,
                        is_resolved=1,
                    )
                    feedback_message_ids.append(feedback_id)
                    version_id = repo.create_goal_version(
                        connection,
                        daily_goal_id=daily_goal_id,
                        version_no=2,
                        is_active=1,
                        main_goal="缩小范围：完成 DayPilot Goal Critic 质量门的可验收产出",
                        goal_reason="根据用户反馈缩小今日目标范围。",
                        success_criteria=["交付 Goal Critic 质量门", "记录验收结果"],
                        estimated_minutes=90,
                        difficulty_level=4,
                        minimum_version="Goal Critic 质量门有可检查记录。",
                        stretch_challenge="补充失败样例。",
                        avoid_today=json.dumps(["不要扩展复制和重新生成"], ensure_ascii=False),
                        goal_type="coding",
                        revision_source="user_feedback",
                        feedback_message_id=feedback_id,
                    )
                    repo.update_feedback_message(
                        connection,
                        feedback_id,
                        after_version_id=version_id,
                        is_resolved=1,
                    )

                daily_goal_ids.append(daily_goal_id)
                active_version_ids.append(version_id)
                if index < 5 or include_friday_checkin:
                    checkin_id = repo.create_daily_checkin(
                        connection,
                        daily_goal_id=daily_goal_id,
                        checkin_date=goal_date,
                        week_id="2026-W24",
                        completion_text=f"完成 {item}，留下可复查记录。",
                        felt_difficulty=felt,
                        tomorrow_direction=direction,
                        parsed_completion_rate=rate,
                        completed_items=[item],
                        unfinished_items=unfinished,
                        blockers=[] if felt < 5 else ["范围拆分不够细"],
                        actual_outputs=[f"artifact/{item}"],
                        processor_snapshot={"source": "weekly-report-test"},
                    )
                    checkin_ids.append(checkin_id)

            ability_state_id = repo.create_ability_state(
                connection,
                state_date="2026-06-12",
                current_difficulty=3.4,
                target_difficulty_level=3,
                recent_completion_rate=0.75,
                recent_felt_difficulty_avg=3.8,
                default_estimated_minutes=90,
                preferred_goal_type_weights={"coding": 0.7, "documentation": 0.3},
                short_term_preferences={},
                long_term_preferences_snapshot={},
                avoid_patterns_snapshot=["目标太大", "周报流水账"],
                adjustment_direction="hold",
                update_reason="Seeded weekly report state.",
                is_current=1,
            )
    finally:
        connection.close()

    return {
        "daily_goal_ids": daily_goal_ids,
        "active_version_ids": active_version_ids,
        "checkin_ids": checkin_ids,
        "feedback_message_ids": feedback_message_ids,
        "ability_state_id": ability_state_id,
    }


def main() -> None:
    test_weekly_report_generates_persists_snapshot_and_focus()
    test_schema_valid_vague_report_fails_quality_review()
    test_deepseek_weekly_report_schema_overflow_uses_semantic_repair()
    test_deepseek_weekly_report_repair_failure_falls_back_to_mock_once()
    test_weekly_report_rejects_before_friday_checkin()
    print("PASS: POST /api/weekly-report/generate creates weekly reports and source snapshots")


if __name__ == "__main__":
    main()
