from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.ai_client import (
    LLMAllModelsFailedError,
    LLMConfigurationError,
    LLMMalformedResponseError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMTransportError,
    ModelFallbackLLMClient,
    create_openrouter_llm_client,
    parse_openrouter_response,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService


class RecordingOps(DisabledOpsNotificationService):
    def __init__(self) -> None:
        self.messages: list[str] = []

    @property
    def enabled(self) -> bool:
        return True

    async def notify(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> bool:
        self.messages.append(f"{severity}:{reason}:{correlation_id}")
        return True


class ScriptedTransport:
    def __init__(self, outcomes: list[LLMResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.models: list[str] = []

    async def complete_json(self, *, model: str, request: LLMRequest) -> LLMResponse:
        self.models.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="safe")],
        json_schema={"type": "object"},
        correlation_id="cid-1",
        min_confidence=0.72,
    )


def _response(confidence: float, *, model: str = "model") -> LLMResponse:
    return LLMResponse(model=model, content={"confidence": confidence}, confidence=confidence)


@pytest.mark.asyncio
async def test_primary_success_uses_first_configured_model() -> None:
    transport = ScriptedTransport([_response(0.9)])
    client = ModelFallbackLLMClient(
        models=("primary", "fallback"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_request())

    assert response.confidence == 0.9
    assert transport.models == ["primary"]
    assert [attempt.status for attempt in attempts] == ["success"]


@pytest.mark.asyncio
async def test_transport_error_falls_back_to_next_model() -> None:
    transport = ScriptedTransport([LLMTransportError("429", retryable=True), _response(0.88)])
    client = ModelFallbackLLMClient(
        models=("primary", "fallback"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_request())

    assert response.confidence == 0.88
    assert transport.models == ["primary", "fallback"]
    assert [attempt.status for attempt in attempts] == ["failed", "success"]


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_falls_back_to_next_model() -> None:
    transport = ScriptedTransport(
        [LLMTransportError("OpenRouter timeout", retryable=True), _response(0.88)]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "fallback"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_request())

    assert response.confidence == 0.88
    assert transport.models == ["primary", "fallback"]
    assert attempts[0].error_type == "LLMTransportError"


@pytest.mark.asyncio
async def test_non_retryable_transport_error_stops_without_wasting_fallbacks() -> None:
    transport = ScriptedTransport([LLMTransportError("OpenRouter HTTP 401", retryable=False)])
    client = ModelFallbackLLMClient(
        models=("primary", "fallback", "final"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    with pytest.raises(LLMAllModelsFailedError) as exc_info:
        await client.complete_json(_request())

    assert transport.models == ["primary"]
    assert len(exc_info.value.attempts) == 1


@pytest.mark.asyncio
async def test_low_confidence_retries_until_final_model() -> None:
    transport = ScriptedTransport([_response(0.2), _response(0.5), _response(0.91)])
    client = ModelFallbackLLMClient(
        models=("primary", "fallback", "final"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_request())

    assert response.confidence == 0.91
    assert transport.models == ["primary", "fallback", "final"]
    assert [attempt.status for attempt in attempts] == [
        "low_confidence",
        "low_confidence",
        "success",
    ]


@pytest.mark.asyncio
async def test_all_models_failed_returns_typed_error_without_prompt() -> None:
    transport = ScriptedTransport(
        [
            LLMTransportError("timeout", retryable=True),
            LLMTransportError("429", retryable=True),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "fallback"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    with pytest.raises(LLMAllModelsFailedError) as exc_info:
        await client.complete_json(_request())

    assert [attempt.model for attempt in exc_info.value.attempts] == ["primary", "fallback"]
    assert "safe" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_malformed_response_falls_back_without_leaking_raw_listing() -> None:
    transport = ScriptedTransport(
        [
            LLMMalformedResponseError("Malformed OpenRouter response id=req-1 model=primary"),
            _response(0.9),
        ]
    )
    ops = RecordingOps()
    client = ModelFallbackLLMClient(
        models=("primary", "fallback"),
        transport=transport,
        ops_notifier=ops,
    )

    response, attempts = await client.complete_json(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Yunusobod +998901234567 raw listing")],
            json_schema={"type": "object"},
            correlation_id="cid-raw",
            min_confidence=0.72,
        )
    )

    assert response.confidence == 0.9
    assert [attempt.status for attempt in attempts] == ["failed", "success"]
    joined_ops = "\n".join(ops.messages)
    assert "Yunusobod" not in joined_ops
    assert "+998901234567" not in joined_ops


def test_parse_openrouter_response_extracts_json_content() -> None:
    body: dict[str, Any] = {
        "id": "req-1",
        "model": "configured",
        "choices": [{"message": {"content": '{"confidence": 0.8}'}}],
        "usage": {"prompt_tokens": 10},
    }

    response = parse_openrouter_response(body)

    assert response.model == "configured"
    assert response.confidence == 0.8
    assert response.usage == {"prompt_tokens": 10}


def test_parse_openrouter_malformed_response_raises_typed_error_without_content() -> None:
    body: dict[str, Any] = {
        "id": "req-1",
        "model": "primary",
        "choices": [{"message": {"content": "raw listing +998901234567 not json"}}],
    }

    with pytest.raises(LLMMalformedResponseError) as exc_info:
        parse_openrouter_response(body)

    message = str(exc_info.value)
    assert "req-1" in message
    assert "primary" in message
    assert "raw listing" not in message
    assert "+998901234567" not in message


def test_openrouter_client_factory_uses_typed_settings_model_sequence() -> None:
    client = create_openrouter_llm_client(
        settings=Settings(
            environment="test",
            openrouter_api_key=SecretStr("or_fake_key_for_tests_123456789"),
            openrouter_model_sequence="model-a, model-b",
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    assert isinstance(client, ModelFallbackLLMClient)


def test_openrouter_client_factory_requires_api_key() -> None:
    with pytest.raises(LLMConfigurationError):
        create_openrouter_llm_client(
            settings=Settings(environment="test", openrouter_api_key=None),
            ops_notifier=DisabledOpsNotificationService(),
        )
