from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config.settings import DayPilotSettings, load_daypilot_settings  # noqa: E402
from backend.services.llm_client import DeepSeekJsonClient, generate_json_with_fallback  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeUrlopenSequence:
    def __init__(self, *items: dict[str, Any] | BaseException) -> None:
        self.items = list(items)
        self.calls = 0
        self.requests: list[Any] = []

    def __call__(self, request: Any, *args: object, **kwargs: object) -> FakeResponse:
        self.calls += 1
        self.requests.append(request)
        if not self.items:
            raise AssertionError("No fake DeepSeek response left")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)


def _settings(
    *,
    mode: str = "deepseek",
    key: str | None = "test-key",
    log_enabled: bool = False,
    log_dir: str | None = None,
) -> DayPilotSettings:
    return DayPilotSettings(
        llm_mode=mode,
        deepseek_api_key=key,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=3,
        deepseek_max_tokens=200,
        deepseek_thinking="disabled",
        llm_log_enabled=log_enabled,
        llm_log_dir=log_dir or str(ROOT / "data" / "llm_logs"),
    )


def _deepseek_payload(content: str) -> dict[str, Any]:
    return {
        "id": "response-1",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_dotenv_loads_and_environment_overrides() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        dotenv = Path(temp_dir) / ".env"
        dotenv.write_text(
            "\n".join(
                [
                    "DAYPILOT_LLM_MODE=deepseek",
                    "DEEPSEEK_MODEL=deepseek-v4-flash",
                    "DEEPSEEK_TIMEOUT_SECONDS=12",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_daypilot_settings(
            env={"DEEPSEEK_MODEL": "deepseek-v4-pro"},
            dotenv_path=dotenv,
        )

        assert settings.llm_mode == "deepseek"
        assert settings.deepseek_model == "deepseek-v4-pro"
        assert settings.deepseek_timeout_seconds == 12
        assert settings.llm_log_enabled is True


def test_prefer_dotenv_keeps_project_key_over_environment() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        dotenv = Path(temp_dir) / ".env"
        dotenv.write_text(
            "\n".join(
                [
                    "DAYPILOT_LLM_MODE=deepseek",
                    "DEEPSEEK_API_KEY=project-key-1234",
                    "DEEPSEEK_MODEL=deepseek-v4-pro",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_daypilot_settings(
            env={
                "DAYPILOT_PREFER_DOTENV": "1",
                "DAYPILOT_LLM_MODE": "mock",
                "DEEPSEEK_API_KEY": "bad-shell-key-mode",
                "DEEPSEEK_MODEL": "bad-shell-model",
            },
            dotenv_path=dotenv,
        )

        assert settings.llm_mode == "deepseek"
        assert settings.deepseek_api_key == "project-key-1234"
        assert settings.deepseek_model == "deepseek-v4-pro"


def test_prefer_dotenv_allows_environment_key_when_dotenv_key_is_blank() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        dotenv = Path(temp_dir) / ".env"
        dotenv.write_text(
            "\n".join(
                [
                    "DAYPILOT_LLM_MODE=deepseek",
                    "DEEPSEEK_API_KEY=",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_daypilot_settings(
            env={
                "DAYPILOT_PREFER_DOTENV": "1",
                "DEEPSEEK_API_KEY": "shell-key-1234",
            },
            dotenv_path=dotenv,
        )

        assert settings.llm_mode == "deepseek"
        assert settings.deepseek_api_key == "shell-key-1234"


def test_mock_mode_never_calls_deepseek() -> None:
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = _raise_if_called  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "json"}],
            mock_generate=lambda: {"ok": True},
            settings=_settings(mode="mock"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert result.output == {"ok": True}
    assert result.metadata["llm_mode_used"] == "mock"
    assert result.metadata["fallback_reason"] is None


def test_auto_mode_without_key_falls_back_to_mock() -> None:
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = _raise_if_called  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "json"}],
            mock_generate=lambda: {"ok": "mock"},
            settings=_settings(mode="auto", key=None),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert result.output == {"ok": "mock"}
    assert result.metadata["llm_mode_used"] == "mock"
    assert result.metadata["fallback_reason"] == "missing_deepseek_api_key"
    assert result.metadata["repair_attempted"] is False


def test_deepseek_client_parses_valid_json_content() -> None:
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
            _deepseek_payload('{"ok": true}')
        )
        output, metadata = DeepSeekJsonClient(_settings()).create_json(
            [{"role": "user", "content": "return json"}]
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert output == {"ok": True}
    assert metadata["model_name"] == "deepseek-v4-pro"
    assert metadata["response_id"] == "response-1"


def test_deepseek_empty_or_non_json_content_falls_back() -> None:
    for content in ("", "not json"):
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload(content)
            )
            result = generate_json_with_fallback(
                task_name="unit",
                prompt_version_deepseek="unit_deepseek",
                prompt_version_mock="unit_mock",
                mock_model_name="mock-unit",
                build_messages=lambda soul: [{"role": "user", "content": "json"}],
                mock_generate=lambda: {"ok": "mock"},
                settings=_settings(mode="deepseek"),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result.output == {"ok": "mock"}
        assert result.metadata["llm_mode_used"] == "mock"
        assert result.metadata["fallback_reason"]


def test_deepseek_schema_failure_falls_back() -> None:
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
            _deepseek_payload('{"bad": true}')
        )
        result = generate_json_with_fallback(
            task_name="unit",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "json"}],
            mock_generate=lambda: {"ok": "mock"},
            validator=_require_ok,
            settings=_settings(mode="deepseek"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert result.output == {"ok": "mock"}
    assert "initial_failure=missing_ok" in result.metadata["fallback_reason"]
    assert "repair_failure=missing_ok" in result.metadata["fallback_reason"]
    assert result.metadata["repair_attempted"] is True
    assert result.metadata["repair_succeeded"] is False


def test_deepseek_schema_failure_repairs_to_valid_json_without_mock() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        sequence = FakeUrlopenSequence(
            _deepseek_payload('{"bad": true}'),
            _deepseek_payload('{"ok": true}'),
        )
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = sequence  # type: ignore[assignment]
            result = generate_json_with_fallback(
                task_name="unit_schema_repair",
                prompt_version_deepseek="unit_deepseek",
                prompt_version_mock="unit_mock",
                mock_model_name="mock-unit",
                build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
                mock_generate=lambda: {"ok": "mock"},
                validator=_require_ok,
                settings=_settings(mode="deepseek", log_enabled=True, log_dir=temp_dir),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert sequence.calls == 2
        assert result.output == {"ok": True}
        assert result.metadata["llm_mode_used"] == "deepseek"
        assert result.metadata["repair_attempted"] is True
        assert result.metadata["repair_succeeded"] is True
        assert result.metadata["initial_failure_reason"] == "missing_ok"
        error_records = _read_log_records(Path(temp_dir), "errors")
        deepseek_records = _read_log_records(Path(temp_dir), "deepseek")
        assert len(error_records) == 1
        assert len(deepseek_records) == 1
        assert error_records[0]["attempt"] == "initial"
        assert deepseek_records[0]["attempt"] == "repair"
        assert deepseek_records[0]["repair_of_event_id"] == error_records[0]["event_id"]
        assert _read_log_records(Path(temp_dir), "mock") == []

        repair_payload = json.loads(sequence.requests[1].data.decode("utf-8"))
        repair_user = json.loads(repair_payload["messages"][1]["content"])
        assert repair_user["previous_raw_output"] == {"bad": True}
        assert repair_user["failure_reason"] == "missing_ok"


def test_deepseek_initial_output_normalized_before_validation() -> None:
    sequence = FakeUrlopenSequence(_deepseek_payload('{"ok": "yes"}'))
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = sequence  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit_initial_normalizer",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
            mock_generate=lambda: {"ok": "mock"},
            validator=_require_ok,
            normalizer=lambda payload: {"ok": True} if payload.get("ok") == "yes" else payload,
            settings=_settings(mode="deepseek"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert sequence.calls == 1
    assert result.output == {"ok": True}
    assert result.metadata["llm_mode_used"] == "deepseek"
    assert result.metadata["repair_attempted"] is False
    assert result.metadata["fallback_reason"] is None


def test_deepseek_repair_output_normalized_and_receives_hint() -> None:
    sequence = FakeUrlopenSequence(
        _deepseek_payload('{"bad": true}'),
        _deepseek_payload('{"ok": "yes"}'),
    )
    repair_hint = {"schema": "unit.v1", "required": ["ok"]}
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = sequence  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit_repair_normalizer",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
            mock_generate=lambda: {"ok": "mock"},
            validator=_require_ok,
            normalizer=lambda payload: {"ok": True} if payload.get("ok") == "yes" else payload,
            repair_hint=repair_hint,
            settings=_settings(mode="deepseek"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert sequence.calls == 2
    assert result.output == {"ok": True}
    assert result.metadata["llm_mode_used"] == "deepseek"
    assert result.metadata["repair_attempted"] is True
    assert result.metadata["repair_succeeded"] is True

    repair_payload = json.loads(sequence.requests[1].data.decode("utf-8"))
    repair_user = json.loads(repair_payload["messages"][1]["content"])
    assert repair_user["repair_hint"] == repair_hint


def test_deepseek_non_json_content_repairs_to_valid_json() -> None:
    sequence = FakeUrlopenSequence(
        _deepseek_payload("not json"),
        _deepseek_payload('{"ok": true}'),
    )
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = sequence  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit_non_json_repair",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
            mock_generate=lambda: {"ok": "mock"},
            validator=_require_ok,
            settings=_settings(mode="deepseek"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert sequence.calls == 2
    assert result.output == {"ok": True}
    assert result.metadata["repair_attempted"] is True
    assert result.metadata["initial_failure_reason"] == "deepseek_content_not_json"


def test_deepseek_timeout_repairs_to_valid_json() -> None:
    sequence = FakeUrlopenSequence(
        TimeoutError("timed out"),
        _deepseek_payload('{"ok": true}'),
    )
    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = sequence  # type: ignore[assignment]
        result = generate_json_with_fallback(
            task_name="unit_timeout_repair",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
            mock_generate=lambda: {"ok": "mock"},
            validator=_require_ok,
            settings=_settings(mode="deepseek"),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert sequence.calls == 2
    assert result.output == {"ok": True}
    assert result.metadata["initial_failure_reason"] == "deepseek_timeout"


def test_deepseek_success_writes_jsonl_log() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload('{"ok": true}')
            )
            result = generate_json_with_fallback(
                task_name="unit_success",
                prompt_version_deepseek="unit_deepseek",
                prompt_version_mock="unit_mock",
                mock_model_name="mock-unit",
                build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
                mock_generate=lambda: {"ok": "mock"},
                validator=_require_ok,
                settings=_settings(mode="deepseek", log_enabled=True, log_dir=temp_dir),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result.output == {"ok": True}
        records = _read_log_records(Path(temp_dir), "deepseek")
        assert len(records) == 1
        record = records[0]
        assert record["task_name"] == "unit_success"
        assert record["messages"][0]["content"] == "return ok json"
        assert record["raw_output"] == {"ok": True}
        assert record["validated_output"] == {"ok": True}
        assert record["validator_status"] == "passed"
        assert record["attempt"] == "initial"
        _assert_no_secret_in_logs(Path(temp_dir))


def test_deepseek_schema_failure_logs_error_then_mock_fallback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload('{"bad": true}')
            )
            result = generate_json_with_fallback(
                task_name="unit_schema_failure",
                prompt_version_deepseek="unit_deepseek",
                prompt_version_mock="unit_mock",
                mock_model_name="mock-unit",
                build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
                mock_generate=lambda: {"ok": True},
                validator=_require_ok,
                settings=_settings(mode="deepseek", log_enabled=True, log_dir=temp_dir),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result.output == {"ok": True}
        error_records = _read_log_records(Path(temp_dir), "errors")
        mock_records = _read_log_records(Path(temp_dir), "mock")
        assert len(error_records) == 2
        assert error_records[0]["validator_status"] == "failed"
        assert error_records[0]["raw_output"] == {"bad": True}
        assert error_records[0]["fallback_reason"] == "missing_ok"
        assert error_records[0]["attempt"] == "initial"
        assert error_records[1]["attempt"] == "repair"
        assert error_records[1]["repair_of_event_id"] == error_records[0]["event_id"]
        assert len(mock_records) == 1
        assert "initial_failure=missing_ok" in mock_records[0]["fallback_reason"]
        assert "repair_failure=missing_ok" in mock_records[0]["fallback_reason"]
        assert mock_records[0]["attempt"] == "mock_fallback"


def test_deepseek_non_json_logs_error_then_mock_fallback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload("not json")
            )
            result = generate_json_with_fallback(
                task_name="unit_non_json",
                prompt_version_deepseek="unit_deepseek",
                prompt_version_mock="unit_mock",
                mock_model_name="mock-unit",
                build_messages=lambda soul: [{"role": "user", "content": "return ok json"}],
                mock_generate=lambda: {"ok": True},
                validator=_require_ok,
                settings=_settings(mode="deepseek", log_enabled=True, log_dir=temp_dir),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert result.output == {"ok": True}
        error_records = _read_log_records(Path(temp_dir), "errors")
        assert error_records[0]["error"] == "deepseek_content_not_json"
        assert error_records[0]["attempt"] == "initial"
        assert error_records[1]["attempt"] == "repair"
        mock_record = _read_log_records(Path(temp_dir), "mock")[0]
        assert mock_record["llm_mode_used"] == "mock"
        assert mock_record["attempt"] == "mock_fallback"


def test_mock_mode_writes_only_mock_log() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        result = generate_json_with_fallback(
            task_name="unit_mock_only",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "mock prompt"}],
            mock_generate=lambda: {"ok": True},
            validator=_require_ok,
            settings=_settings(mode="mock", log_enabled=True, log_dir=temp_dir),
        )

        assert result.output == {"ok": True}
        assert len(_read_log_records(Path(temp_dir), "mock")) == 1
        assert _read_log_records(Path(temp_dir), "mock")[0]["attempt"] == "initial"
        assert _read_log_records(Path(temp_dir), "deepseek") == []
        assert _read_log_records(Path(temp_dir), "errors") == []


def test_llm_logging_can_be_disabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        result = generate_json_with_fallback(
            task_name="unit_disabled",
            prompt_version_deepseek="unit_deepseek",
            prompt_version_mock="unit_mock",
            mock_model_name="mock-unit",
            build_messages=lambda soul: [{"role": "user", "content": "mock prompt"}],
            mock_generate=lambda: {"ok": True},
            validator=_require_ok,
            settings=_settings(mode="mock", log_enabled=False, log_dir=temp_dir),
        )

        assert result.output == {"ok": True}
        assert not list(Path(temp_dir).rglob("*.jsonl"))


def test_direct_deepseek_client_call_writes_log() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(  # type: ignore[assignment]
                _deepseek_payload('{"ok": true}')
            )
            output, metadata = DeepSeekJsonClient(
                _settings(mode="deepseek", log_enabled=True, log_dir=temp_dir)
            ).create_json(
                [{"role": "user", "content": "direct check"}],
                task_name="deepseek_connection_check",
                prompt_version="manual_connection_check",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert output == {"ok": True}
        assert metadata["model_name"] == "deepseek-v4-pro"
        records = _read_log_records(Path(temp_dir), "deepseek")
        assert records[0]["task_name"] == "deepseek_connection_check"
        assert records[0]["validator_status"] == "not_applicable"
        assert records[0]["attempt"] == "initial"
        _assert_no_secret_in_logs(Path(temp_dir))


def test_direct_deepseek_client_repairs_non_json_content() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        sequence = FakeUrlopenSequence(
            _deepseek_payload("not json"),
            _deepseek_payload('{"ok": true}'),
        )
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = sequence  # type: ignore[assignment]
            output, metadata = DeepSeekJsonClient(
                _settings(mode="deepseek", log_enabled=True, log_dir=temp_dir)
            ).create_json(
                [{"role": "user", "content": "direct check"}],
                task_name="deepseek_connection_check",
                prompt_version="manual_connection_check",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        assert sequence.calls == 2
        assert output == {"ok": True}
        assert metadata["repair_attempted"] is True
        assert metadata["repair_succeeded"] is True
        assert metadata["initial_failure_reason"] == "deepseek_content_not_json"
        errors = _read_log_records(Path(temp_dir), "errors")
        deepseek = _read_log_records(Path(temp_dir), "deepseek")
        assert errors[0]["attempt"] == "initial"
        assert deepseek[0]["attempt"] == "repair"
        assert deepseek[0]["repair_of_event_id"] == errors[0]["event_id"]
        _assert_no_secret_in_logs(Path(temp_dir))


def _require_ok(payload: dict[str, Any]) -> None:
    if payload.get("ok") is not True:
        raise ValueError("missing_ok")


def _read_log_records(root: Path, stream: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / stream).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _assert_no_secret_in_logs(root: Path) -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.jsonl"))
    assert "test-key" not in text
    assert "Authorization" not in text
    assert "Bearer" not in text


def _raise_if_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("DeepSeek should not be called in mock mode")


def main() -> None:
    test_dotenv_loads_and_environment_overrides()
    test_prefer_dotenv_keeps_project_key_over_environment()
    test_prefer_dotenv_allows_environment_key_when_dotenv_key_is_blank()
    test_mock_mode_never_calls_deepseek()
    test_auto_mode_without_key_falls_back_to_mock()
    test_deepseek_client_parses_valid_json_content()
    test_deepseek_empty_or_non_json_content_falls_back()
    test_deepseek_schema_failure_falls_back()
    test_deepseek_schema_failure_repairs_to_valid_json_without_mock()
    test_deepseek_initial_output_normalized_before_validation()
    test_deepseek_repair_output_normalized_and_receives_hint()
    test_deepseek_non_json_content_repairs_to_valid_json()
    test_deepseek_timeout_repairs_to_valid_json()
    test_deepseek_success_writes_jsonl_log()
    test_deepseek_schema_failure_logs_error_then_mock_fallback()
    test_deepseek_non_json_logs_error_then_mock_fallback()
    test_mock_mode_writes_only_mock_log()
    test_llm_logging_can_be_disabled()
    test_direct_deepseek_client_call_writes_log()
    test_direct_deepseek_client_repairs_non_json_content()
    print("PASS: LLM settings, DeepSeek JSON parsing, and fallback behavior verified")


if __name__ == "__main__":
    main()
