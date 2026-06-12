from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.config.settings import DayPilotSettings, load_daypilot_settings
from backend.services.llm_logging import write_llm_log
from backend.services.soul_context import load_soul_context


Validator = Callable[[dict[str, Any]], None]
MessageBuilder = Callable[[str], list[dict[str, str]]]
MockGenerator = Callable[[], dict[str, Any]]
Normalizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class DeepSeekCallFailure:
    reason: str
    raw_output: Any = None
    response_id: str | None = None
    usage: Any = None
    model_name: str | None = None
    event_id: str | None = None


class LLMCallError(RuntimeError):
    """Raised when a model call cannot produce a valid structured result."""

    def __init__(self, message: str, *, failure: DeepSeekCallFailure | None = None) -> None:
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True)
class LLMGenerationResult:
    output: dict[str, Any]
    metadata: dict[str, Any]


class DeepSeekJsonClient:
    def __init__(self, settings: DayPilotSettings) -> None:
        self.settings = settings

    def create_json(
        self,
        messages: list[dict[str, str]],
        *,
        task_name: str = "direct_deepseek_json",
        prompt_version: str | None = "direct_deepseek_json",
        llm_mode_requested: str | None = None,
        soul_loaded: bool | None = None,
        soul_path: str | None = None,
        log_event: bool = True,
        allow_repair: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.settings.deepseek_api_key:
            error = "missing_deepseek_api_key"
            failure = DeepSeekCallFailure(reason=error)
            if log_event:
                event_id = self._log_error(
                    messages,
                    task_name=task_name,
                    prompt_version=prompt_version,
                    llm_mode_requested=llm_mode_requested,
                    error=error,
                    soul_loaded=soul_loaded,
                    soul_path=soul_path,
                    attempt="initial",
                )
                failure = _with_event_id(failure, event_id)
            raise _llm_call_error(error, failure)

        try:
            return self._create_json_once(
                messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt="initial",
            )
        except LLMCallError as exc:
            if not allow_repair:
                raise
            initial_failure = _failure_from_exception(exc)
            repair_result = self._attempt_repair(
                original_messages=messages,
                initial_failure=initial_failure,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
            )
            if repair_result is not None:
                return repair_result
            repair_failure = getattr(self, "_last_repair_failure", None)
            repair_reason = (
                repair_failure.reason
                if isinstance(repair_failure, DeepSeekCallFailure)
                else "repair_failed"
            )
            combined = _combined_failure_reason(initial_failure.reason, repair_reason)
            raise _llm_call_error(combined, DeepSeekCallFailure(reason=combined)) from exc

    def _create_json_once(
        self,
        messages: list[dict[str, str]],
        *,
        task_name: str,
        prompt_version: str | None,
        llm_mode_requested: str | None,
        soul_loaded: bool | None,
        soul_path: str | None,
        log_event: bool,
        attempt: str,
        repair_of_event_id: str | None = None,
        repair_reason: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "stream": False,
            "max_tokens": self.settings.deepseek_max_tokens,
            "thinking": {"type": self.settings.deepseek_thinking},
        }
        request = urllib.request.Request(
            f"{self.settings.deepseek_base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            },
            method="POST",
        )

        raw_payload: str | None = None
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.deepseek_timeout_seconds,
            ) as response:
                raw_payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            self._raise_failure(
                f"deepseek_http_{exc.code}:{detail}",
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )
        except urllib.error.URLError as exc:
            self._raise_failure(
                f"deepseek_network_error:{exc.reason}",
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )
        except TimeoutError as exc:
            self._raise_failure(
                "deepseek_timeout",
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )

        try:
            response_payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            self._raise_failure(
                "deepseek_response_not_json",
                raw_output=raw_payload,
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )

        try:
            message = response_payload["choices"][0]["message"]
            content = str(message.get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            self._raise_failure(
                "deepseek_response_missing_content",
                raw_output=response_payload,
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )
        response_id = response_payload.get("id")
        usage = response_payload.get("usage")
        model_name = response_payload.get("model") or self.settings.deepseek_model

        if not content:
            self._raise_failure(
                "deepseek_empty_content",
                raw_output=response_payload,
                response_id=response_id,
                usage=usage,
                model_name=model_name,
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
            )

        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            self._raise_failure(
                "deepseek_content_not_json",
                raw_output=content,
                response_id=response_id,
                usage=usage,
                model_name=model_name,
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
                exc=exc,
            )
        if not isinstance(output, dict):
            self._raise_failure(
                "deepseek_json_not_object",
                raw_output=output,
                response_id=response_id,
                usage=usage,
                model_name=model_name,
                messages=messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
            )

        metadata = {
            "provider": "deepseek",
            "model_name": model_name,
            "response_id": response_id,
            "usage": usage,
            "repair_attempted": attempt == "repair",
            "repair_succeeded": attempt == "repair",
        }
        if attempt == "repair":
            metadata.update(
                {
                    "initial_failure_reason": repair_reason,
                    "repair_of_event_id": repair_of_event_id,
                }
            )
        if log_event:
            write_llm_log(
                self.settings,
                "deepseek",
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested or self.settings.llm_mode,
                llm_mode_used="deepseek",
                provider="deepseek",
                model_name=model_name,
                messages=messages,
                raw_output=output,
                validated_output=output,
                validator_status="not_applicable",
                error=None,
                fallback_reason=None,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                usage=usage,
                response_id=response_id,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
            )
        return output, metadata

    def _attempt_repair(
        self,
        *,
        original_messages: list[dict[str, str]],
        initial_failure: DeepSeekCallFailure,
        task_name: str,
        prompt_version: str | None,
        llm_mode_requested: str | None,
        soul_loaded: bool | None,
        soul_path: str | None,
        log_event: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        self._last_repair_failure = None
        repair_messages = _repair_messages(
            original_messages,
            initial_failure.raw_output,
            initial_failure.reason,
            repair_hint=None,
        )
        try:
            output, metadata = self._create_json_once(
                repair_messages,
                task_name=task_name,
                prompt_version=_repair_prompt_version(prompt_version),
                llm_mode_requested=llm_mode_requested,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                log_event=log_event,
                attempt="repair",
                repair_of_event_id=initial_failure.event_id,
                repair_reason=initial_failure.reason,
            )
        except LLMCallError as exc:
            self._last_repair_failure = _failure_from_exception(exc)
            return None

        metadata.update(
            {
                "repair_attempted": True,
                "repair_succeeded": True,
                "initial_failure_reason": initial_failure.reason,
                "repair_of_event_id": initial_failure.event_id,
            }
        )
        return output, metadata

    def _raise_failure(
        self,
        reason: str,
        *,
        messages: list[dict[str, str]],
        task_name: str,
        prompt_version: str | None,
        llm_mode_requested: str | None,
        soul_loaded: bool | None,
        soul_path: str | None,
        log_event: bool,
        attempt: str,
        raw_output: Any = None,
        response_id: str | None = None,
        usage: Any = None,
        model_name: str | None = None,
        repair_of_event_id: str | None = None,
        repair_reason: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        failure = DeepSeekCallFailure(
            reason=reason,
            raw_output=raw_output,
            response_id=response_id,
            usage=usage,
            model_name=model_name,
        )
        if log_event:
            event_id = self._log_error(
                messages,
                task_name=task_name,
                prompt_version=prompt_version,
                llm_mode_requested=llm_mode_requested,
                error=reason,
                raw_output=raw_output,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                usage=usage,
                response_id=response_id,
                attempt=attempt,
                repair_of_event_id=repair_of_event_id,
                repair_reason=repair_reason,
            )
            failure = _with_event_id(failure, event_id)
        error = _llm_call_error(reason, failure)
        if exc is not None:
            raise error from exc
        raise error

    def _log_error(
        self,
        messages: list[dict[str, str]],
        *,
        task_name: str,
        prompt_version: str | None,
        llm_mode_requested: str | None,
        error: str,
        raw_output: Any = None,
        soul_loaded: bool | None = None,
        soul_path: str | None = None,
        usage: Any = None,
        response_id: str | None = None,
        attempt: str | None = None,
        repair_of_event_id: str | None = None,
        repair_reason: str | None = None,
    ) -> str | None:
        event_id = str(uuid.uuid4())
        path = write_llm_log(
            self.settings,
            "errors",
            event_id=event_id,
            task_name=task_name,
            prompt_version=prompt_version,
            llm_mode_requested=llm_mode_requested or self.settings.llm_mode,
            llm_mode_used="deepseek",
            provider="deepseek",
            model_name=self.settings.deepseek_model,
            messages=messages,
            raw_output=raw_output,
            validated_output=None,
            validator_status="failed",
            error=error,
            fallback_reason=error,
            soul_loaded=soul_loaded,
            soul_path=soul_path,
            usage=usage,
            response_id=response_id,
            attempt=attempt,
            repair_of_event_id=repair_of_event_id,
            repair_reason=repair_reason,
        )
        return event_id if path is not None else None


def generate_json_with_fallback(
    *,
    task_name: str,
    prompt_version_deepseek: str,
    prompt_version_mock: str,
    mock_model_name: str,
    build_messages: MessageBuilder,
    mock_generate: MockGenerator,
    validator: Validator | None = None,
    normalizer: Normalizer | None = None,
    repair_hint: dict[str, Any] | None = None,
    settings: DayPilotSettings | None = None,
    soul_path: str | Path | None = None,
) -> LLMGenerationResult:
    resolved_settings = settings or load_daypilot_settings()
    soul = load_soul_context(soul_path) if soul_path is not None else load_soul_context()
    mode_requested = resolved_settings.llm_mode
    messages = build_messages(soul.content)

    if mode_requested == "mock":
        return _mock_result(
            settings=resolved_settings,
            task_name=task_name,
            prompt_version=prompt_version_mock,
            mock_model_name=mock_model_name,
            messages=messages,
            mock_generate=mock_generate,
            validator=validator,
            normalizer=normalizer,
            mode_requested=mode_requested,
            soul_loaded=soul.loaded,
            soul_path=soul.path,
            fallback_reason=None,
            extra_metadata=None,
        )

    if not resolved_settings.has_deepseek_key:
        return _mock_result(
            settings=resolved_settings,
            task_name=task_name,
            prompt_version=prompt_version_mock,
            mock_model_name=mock_model_name,
            messages=messages,
            mock_generate=mock_generate,
            validator=validator,
            normalizer=normalizer,
            mode_requested=mode_requested,
            soul_loaded=soul.loaded,
            soul_path=soul.path,
            fallback_reason="missing_deepseek_api_key",
            extra_metadata={"repair_attempted": False, "repair_succeeded": False},
        )

    client = DeepSeekJsonClient(resolved_settings)
    initial_failure: DeepSeekCallFailure | None = None
    call_metadata: dict[str, Any] = {}
    initial_output: dict[str, Any] | None = None
    initial_validator_status = "failed"

    try:
        initial_output, call_metadata = client.create_json(
            messages,
            task_name=task_name,
            prompt_version=prompt_version_deepseek,
            llm_mode_requested=mode_requested,
            soul_loaded=soul.loaded,
            soul_path=soul.path,
            log_event=False,
            allow_repair=False,
        )
        initial_output = _apply_normalizer(initial_output, normalizer)
        initial_validator_status, validator_error = _validator_status(initial_output, validator)
        if validator_error is not None:
            initial_failure = DeepSeekCallFailure(
                reason=validator_error,
                raw_output=initial_output,
                response_id=call_metadata.get("response_id"),
                usage=call_metadata.get("usage"),
                model_name=call_metadata.get("model_name") or resolved_settings.deepseek_model,
            )
        else:
            metadata = _deepseek_metadata(
                task_name=task_name,
                mode_requested=mode_requested,
                model_name=call_metadata.get("model_name") or resolved_settings.deepseek_model,
                prompt_version=prompt_version_deepseek,
                soul_loaded=soul.loaded,
                soul_path=soul.path,
                response_id=call_metadata.get("response_id"),
                usage=call_metadata.get("usage"),
                fallback_reason=None,
                repair_attempted=False,
                repair_succeeded=False,
                initial_failure_reason=None,
            )
            write_llm_log(
                resolved_settings,
                "deepseek",
                task_name=task_name,
                prompt_version=prompt_version_deepseek,
                llm_mode_requested=mode_requested,
                llm_mode_used="deepseek",
                provider="deepseek",
                model_name=metadata["model_name"],
                messages=messages,
                raw_output=initial_output,
                validated_output=initial_output,
                validator_status=initial_validator_status,
                error=None,
                fallback_reason=None,
                soul_loaded=soul.loaded,
                soul_path=soul.path,
                response_id=metadata.get("response_id"),
                usage=metadata.get("usage"),
                attempt="initial",
            )
            return LLMGenerationResult(output=initial_output, metadata=metadata)
    except Exception as exc:  # Deliberately broad: model failures must not block local use.
        initial_failure = _failure_from_exception(exc)
        initial_validator_status = "failed"

    if initial_failure is None:
        initial_failure = DeepSeekCallFailure(reason="unknown_deepseek_failure")

    if initial_failure.event_id is None:
        initial_event_id = _write_generation_error(
            resolved_settings,
            messages=messages,
            task_name=task_name,
            prompt_version=prompt_version_deepseek,
            mode_requested=mode_requested,
            model_name=initial_failure.model_name or resolved_settings.deepseek_model,
            raw_output=initial_failure.raw_output,
            validator_status=initial_validator_status,
            error=initial_failure.reason,
            soul_loaded=soul.loaded,
            soul_path=soul.path,
            response_id=initial_failure.response_id,
            usage=initial_failure.usage,
            attempt="initial",
            repair_of_event_id=None,
            repair_reason=None,
        )
        initial_failure = _with_event_id(initial_failure, initial_event_id)

    repair_result, repair_failure = _try_repair_generation(
        client=client,
        settings=resolved_settings,
        original_messages=messages,
        initial_failure=initial_failure,
        task_name=task_name,
        prompt_version=prompt_version_deepseek,
        mode_requested=mode_requested,
        validator=validator,
        normalizer=normalizer,
        repair_hint=repair_hint,
        soul_loaded=soul.loaded,
        soul_path=soul.path,
    )
    if repair_result is not None:
        return repair_result

    repair_reason = repair_failure.reason if repair_failure is not None else "repair_failed"
    fallback_reason = _combined_failure_reason(initial_failure.reason, repair_reason)
    return _mock_result(
        settings=resolved_settings,
        task_name=task_name,
        prompt_version=prompt_version_mock,
        mock_model_name=mock_model_name,
        messages=messages,
        mock_generate=mock_generate,
        validator=validator,
        normalizer=normalizer,
        mode_requested=mode_requested,
        soul_loaded=soul.loaded,
        soul_path=soul.path,
        fallback_reason=fallback_reason,
        extra_metadata={
            "repair_attempted": True,
            "repair_succeeded": False,
            "initial_failure_reason": initial_failure.reason,
            "repair_failure_reason": repair_reason,
            "repair_of_event_id": initial_failure.event_id,
        },
    )


def _try_repair_generation(
    *,
    client: DeepSeekJsonClient,
    settings: DayPilotSettings,
    original_messages: list[dict[str, str]],
    initial_failure: DeepSeekCallFailure,
    task_name: str,
    prompt_version: str,
    mode_requested: str,
    validator: Validator | None,
    normalizer: Normalizer | None,
    repair_hint: dict[str, Any] | None,
    soul_loaded: bool,
    soul_path: str,
) -> tuple[LLMGenerationResult | None, DeepSeekCallFailure | None]:
    repair_messages = _repair_messages(
        original_messages,
        initial_failure.raw_output,
        initial_failure.reason,
        repair_hint=repair_hint,
    )
    repair_prompt_version = _repair_prompt_version(prompt_version)
    repair_output: dict[str, Any] | None = None
    repair_metadata: dict[str, Any] = {}
    repair_validator_status = "failed"

    try:
        repair_output, repair_metadata = client.create_json(
            repair_messages,
            task_name=task_name,
            prompt_version=repair_prompt_version,
            llm_mode_requested=mode_requested,
            soul_loaded=soul_loaded,
            soul_path=soul_path,
            log_event=False,
            allow_repair=False,
        )
        repair_output = _apply_normalizer(repair_output, normalizer)
        repair_validator_status, validator_error = _validator_status(repair_output, validator)
        if validator_error is not None:
            repair_failure = DeepSeekCallFailure(
                reason=validator_error,
                raw_output=repair_output,
                response_id=repair_metadata.get("response_id"),
                usage=repair_metadata.get("usage"),
                model_name=repair_metadata.get("model_name") or settings.deepseek_model,
            )
            event_id = _write_generation_error(
                settings,
                messages=repair_messages,
                task_name=task_name,
                prompt_version=repair_prompt_version,
                mode_requested=mode_requested,
                model_name=repair_failure.model_name or settings.deepseek_model,
                raw_output=repair_failure.raw_output,
                validator_status=repair_validator_status,
                error=repair_failure.reason,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                response_id=repair_failure.response_id,
                usage=repair_failure.usage,
                attempt="repair",
                repair_of_event_id=initial_failure.event_id,
                repair_reason=initial_failure.reason,
            )
            return None, _with_event_id(repair_failure, event_id)

        metadata = _deepseek_metadata(
            task_name=task_name,
            mode_requested=mode_requested,
            model_name=repair_metadata.get("model_name") or settings.deepseek_model,
            prompt_version=prompt_version,
            soul_loaded=soul_loaded,
            soul_path=soul_path,
            response_id=repair_metadata.get("response_id"),
            usage=repair_metadata.get("usage"),
            fallback_reason=None,
            repair_attempted=True,
            repair_succeeded=True,
            initial_failure_reason=initial_failure.reason,
        )
        metadata["repair_of_event_id"] = initial_failure.event_id
        metadata["repair_prompt_version"] = repair_prompt_version
        write_llm_log(
            settings,
            "deepseek",
            task_name=task_name,
            prompt_version=repair_prompt_version,
            llm_mode_requested=mode_requested,
            llm_mode_used="deepseek",
            provider="deepseek",
            model_name=metadata["model_name"],
            messages=repair_messages,
            raw_output=repair_output,
            validated_output=repair_output,
            validator_status=repair_validator_status,
            error=None,
            fallback_reason=None,
            soul_loaded=soul_loaded,
            soul_path=soul_path,
            response_id=metadata.get("response_id"),
            usage=metadata.get("usage"),
            attempt="repair",
            repair_of_event_id=initial_failure.event_id,
            repair_reason=initial_failure.reason,
        )
        return LLMGenerationResult(output=repair_output, metadata=metadata), None
    except Exception as exc:  # Deliberately broad: repair failure should fall through to mock.
        repair_failure = _failure_from_exception(exc)
        if repair_failure.event_id is None:
            event_id = _write_generation_error(
                settings,
                messages=repair_messages,
                task_name=task_name,
                prompt_version=repair_prompt_version,
                mode_requested=mode_requested,
                model_name=repair_failure.model_name or settings.deepseek_model,
                raw_output=repair_failure.raw_output if repair_failure.raw_output is not None else repair_output,
                validator_status=repair_validator_status,
                error=repair_failure.reason,
                soul_loaded=soul_loaded,
                soul_path=soul_path,
                response_id=repair_failure.response_id or repair_metadata.get("response_id"),
                usage=repair_failure.usage or repair_metadata.get("usage"),
                attempt="repair",
                repair_of_event_id=initial_failure.event_id,
                repair_reason=initial_failure.reason,
            )
            repair_failure = _with_event_id(repair_failure, event_id)
        return None, repair_failure


def _mock_result(
    *,
    settings: DayPilotSettings,
    task_name: str,
    prompt_version: str,
    mock_model_name: str,
    messages: list[dict[str, str]],
    mock_generate: MockGenerator,
    validator: Validator | None,
    normalizer: Normalizer | None,
    mode_requested: str,
    soul_loaded: bool,
    soul_path: str,
    fallback_reason: str | None,
    extra_metadata: dict[str, Any] | None,
) -> LLMGenerationResult:
    output = _apply_normalizer(mock_generate(), normalizer)
    validator_status, validator_error = _validator_status(output, validator)
    write_llm_log(
        settings,
        "mock",
        task_name=task_name,
        prompt_version=prompt_version,
        llm_mode_requested=mode_requested,
        llm_mode_used="mock",
        provider="mock",
        model_name=mock_model_name,
        messages=messages,
        raw_output=output,
        validated_output=output if validator_error is None else None,
        validator_status=validator_status,
        error=validator_error,
        fallback_reason=fallback_reason,
        soul_loaded=soul_loaded,
        soul_path=soul_path,
        usage=None,
        response_id=None,
        attempt="mock_fallback" if fallback_reason else "initial",
    )
    metadata = {
        "task_name": task_name,
        "llm_mode_requested": mode_requested,
        "llm_mode_used": "mock",
        "provider": "mock",
        "model_name": mock_model_name,
        "prompt_version": prompt_version,
        "soul_loaded": soul_loaded,
        "soul_path": str(soul_path),
        "fallback_reason": fallback_reason,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return LLMGenerationResult(output=output, metadata=metadata)


def _deepseek_metadata(
    *,
    task_name: str,
    mode_requested: str,
    model_name: str,
    prompt_version: str,
    soul_loaded: bool,
    soul_path: str,
    response_id: str | None,
    usage: Any,
    fallback_reason: str | None,
    repair_attempted: bool,
    repair_succeeded: bool,
    initial_failure_reason: str | None,
) -> dict[str, Any]:
    return {
        "task_name": task_name,
        "llm_mode_requested": mode_requested,
        "llm_mode_used": "deepseek",
        "provider": "deepseek",
        "model_name": model_name,
        "prompt_version": prompt_version,
        "soul_loaded": soul_loaded,
        "soul_path": str(soul_path),
        "fallback_reason": fallback_reason,
        "response_id": response_id,
        "usage": usage,
        "repair_attempted": repair_attempted,
        "repair_succeeded": repair_succeeded,
        "initial_failure_reason": initial_failure_reason,
    }


def _write_generation_error(
    settings: DayPilotSettings,
    *,
    messages: list[dict[str, str]],
    task_name: str,
    prompt_version: str | None,
    mode_requested: str,
    model_name: str,
    raw_output: Any,
    validator_status: str,
    error: str,
    soul_loaded: bool,
    soul_path: str,
    response_id: str | None,
    usage: Any,
    attempt: str,
    repair_of_event_id: str | None,
    repair_reason: str | None,
) -> str | None:
    event_id = str(uuid.uuid4())
    path = write_llm_log(
        settings,
        "errors",
        event_id=event_id,
        task_name=task_name,
        prompt_version=prompt_version,
        llm_mode_requested=mode_requested,
        llm_mode_used="deepseek",
        provider="deepseek",
        model_name=model_name,
        messages=messages,
        raw_output=raw_output,
        validated_output=None,
        validator_status=validator_status,
        error=error,
        fallback_reason=error,
        soul_loaded=soul_loaded,
        soul_path=soul_path,
        response_id=response_id,
        usage=usage,
        attempt=attempt,
        repair_of_event_id=repair_of_event_id,
        repair_reason=repair_reason,
    )
    return event_id if path is not None else None


def _repair_messages(
    original_messages: list[dict[str, str]],
    previous_raw_output: Any,
    failure_reason: str,
    *,
    repair_hint: dict[str, Any] | None,
) -> list[dict[str, str]]:
    hint = repair_hint or {}
    semantic_compression = bool(hint.get("semantic_compression")) or (
        hint.get("repair_mode") == "semantic_compression"
    )
    if semantic_compression:
        system_content = (
            "You repair and semantically compress failed DayPilot structured outputs. "
            "Return only one valid JSON object that conforms to the original task schema. "
            "Do not include Markdown, code fences, comments, or extra prose. You may "
            "merge, shorten, and rewrite list items to satisfy maxItems and maxLength "
            "while preserving the strongest evidence-backed business meaning."
        )
        required_action = (
            "Repair the previous model response by semantically compressing it. Return "
            "exactly one JSON object that satisfies the original task schema. If a list "
            "has too many items, merge related items by project or theme and drop weaker "
            "details. If an item is too long, rewrite it as a shorter sentence instead "
            "of truncating it. Preserve facts, evidence, explicit outcomes, and user "
            "constraints. Do not add fields, Markdown, code fences, comments, or prose."
        )
    else:
        system_content = (
            "You repair failed DayPilot structured outputs. Return only one "
            "valid JSON object that conforms to the original task schema. "
            "Do not include Markdown, code fences, or extra prose. Keep the "
            "original intent and only repair schema/format violations."
        )
        required_action = (
            "Repair the previous model response. Return exactly one JSON object "
            "that satisfies the original task schema. Do not add Markdown, code "
            "fences, comments, or explanatory text. Preserve the business meaning "
            "of the previous response; only fix JSON shape, field names, enum "
            "values, required fields, and simple scalar/list formatting."
        )
    repair_payload = {
        "original_messages": original_messages,
        "previous_raw_output": previous_raw_output,
        "failure_reason": failure_reason,
        "repair_hint": hint,
        "required_action": required_action,
    }
    return [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": json.dumps(repair_payload, ensure_ascii=False, default=str),
        },
    ]


def _repair_prompt_version(prompt_version: str | None) -> str:
    base = prompt_version or "deepseek_json"
    return f"{base}_repair"


def _validator_status(output: dict[str, Any], validator: Validator | None) -> tuple[str, str | None]:
    if validator is None:
        return "not_applicable", None
    try:
        validator(output)
    except Exception as exc:  # noqa: BLE001 - caller decides whether validation blocks.
        return "failed", _safe_error(exc)
    return "passed", None


def _apply_normalizer(
    output: dict[str, Any],
    normalizer: Normalizer | None,
) -> dict[str, Any]:
    if normalizer is None:
        return output
    try:
        normalized = normalizer(output)
    except Exception as exc:  # noqa: BLE001 - caller handles fallback policy.
        failure = DeepSeekCallFailure(
            reason=f"normalizer_failed:{_safe_error(exc)}",
            raw_output=output,
        )
        raise _llm_call_error(failure.reason, failure) from exc
    if not isinstance(normalized, dict):
        failure = DeepSeekCallFailure(
            reason="normalizer_returned_non_object",
            raw_output=output,
        )
        raise _llm_call_error(failure.reason, failure)
    return normalized


def _failure_from_exception(exc: Exception) -> DeepSeekCallFailure:
    failure = getattr(exc, "failure", None)
    if isinstance(failure, DeepSeekCallFailure):
        return failure
    return DeepSeekCallFailure(reason=_safe_error(exc))


def _with_event_id(failure: DeepSeekCallFailure, event_id: str | None) -> DeepSeekCallFailure:
    return DeepSeekCallFailure(
        reason=failure.reason,
        raw_output=failure.raw_output,
        response_id=failure.response_id,
        usage=failure.usage,
        model_name=failure.model_name,
        event_id=event_id,
    )


def _llm_call_error(reason: str, failure: DeepSeekCallFailure) -> LLMCallError:
    return LLMCallError(reason, failure=failure)


def _combined_failure_reason(initial_reason: str, repair_reason: str) -> str:
    return _truncate(f"initial_failure={initial_reason}; repair_failure={repair_reason}", 600)


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return _truncate(text or exc.__class__.__name__, 300)


def _truncate(text: str, limit: int) -> str:
    return text[:limit]
