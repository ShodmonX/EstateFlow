from __future__ import annotations

from dataclasses import replace
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
from estateflow.services.extraction import ListingExtractionRaw, listing_json_schema
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


def _listing_request() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="safe")],
        json_schema=listing_json_schema(),
        correlation_id="cid-listing",
        min_confidence=0.72,
        fallback_policy="listing_quality",
        response_validator=ListingExtractionRaw.model_validate,
    )


def _listing_response(
    confidence: float,
    *,
    model: str,
    **content: Any,
) -> LLMResponse:
    return LLMResponse(
        model=model,
        content={"confidence": confidence, **content},
        confidence=confidence,
    )


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
    assert attempts[0].decision_reason == "retryable_transport_error"
    assert attempts[1].selected is True


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
async def test_listing_policy_does_not_send_explicit_non_rental_junk_to_preview() -> None:
    transport = ScriptedTransport(
        [
            _listing_response(
                0.18,
                model="primary",
                is_rental_announcement=False,
                rental_confidence=0.98,
                description="Kanal qoidalari va umumiy suhbat",
            ),
            _listing_response(
                0.95,
                model="preview",
                is_rental_announcement=True,
                price=500,
                rooms=2,
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_listing_request())

    assert response.model == "primary"
    assert transport.models == ["primary"]
    assert attempts[0].status == "low_confidence"
    assert attempts[0].decision_reason == "explicit_non_rental_no_fallback"
    assert attempts[0].selected is True


@pytest.mark.asyncio
async def test_listing_policy_returns_best_valid_low_confidence_response() -> None:
    transport = ScriptedTransport(
        [
            _listing_response(
                0.51,
                model="primary",
                is_rental_announcement=True,
                rental_confidence=0.9,
                price=450,
                rooms=2,
            ),
            _listing_response(
                0.63,
                model="preview",
                is_rental_announcement=True,
                rental_confidence=0.92,
                price=450,
                rooms=2,
                district="Yunusobod",
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_listing_request())

    assert response.model == "preview"
    assert response.confidence == 0.63
    assert transport.models == ["primary", "preview"]
    assert [attempt.status for attempt in attempts] == ["low_confidence", "low_confidence"]
    assert attempts[0].selected is False
    assert attempts[1].selected is True
    assert attempts[1].decision_reason == "plausible_listing_low_confidence"
    assert attempts[1].selection_reason == "best_valid_low_quality_response_returned"
    assert attempts[1].fallback_trigger_reason == "plausible_listing_low_confidence"


@pytest.mark.asyncio
async def test_listing_policy_sends_plausible_incomplete_result_to_preview() -> None:
    transport = ScriptedTransport(
        [
            _listing_response(
                0.91,
                model="primary",
                is_rental_announcement=True,
                rental_confidence=0.94,
            ),
            _listing_response(
                0.9,
                model="preview",
                is_rental_announcement=True,
                rental_confidence=0.95,
                listing_type="rent",
                price=500,
                rooms=2,
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_listing_request())

    assert response.model == "preview"
    assert transport.models == ["primary", "preview"]
    assert attempts[0].status == "incomplete"
    assert attempts[0].decision_reason == "plausible_listing_incomplete"
    assert attempts[1].status == "success"
    assert attempts[1].decision_reason == "listing_quality_gate_passed"


@pytest.mark.asyncio
async def test_listing_policy_uses_source_required_fields_for_preview_decision() -> None:
    complete_primary = _listing_response(
        0.51,
        model="primary",
        is_rental_announcement=True,
        rental_confidence=0.9,
        price=450,
        rooms=2,
    )
    transport = ScriptedTransport(
        [
            complete_primary,
            _listing_response(
                0.9,
                model="preview",
                is_rental_announcement=True,
                price=450,
                rooms=2,
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )
    request = replace(_listing_request(), required_listing_fields=("price", "rooms"))

    response, attempts = await client.complete_json(request)

    assert response.model == "primary"
    assert transport.models == ["primary"]
    assert attempts[0].decision_reason == "source_required_fields_complete_no_fallback"

    missing_transport = ScriptedTransport(
        [
            complete_primary,
            _listing_response(
                0.9,
                model="preview",
                is_rental_announcement=True,
                price=450,
                rooms=2,
                district="Yunusobod",
            ),
        ]
    )
    missing_client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=missing_transport,
        ops_notifier=RecordingOps(),
    )

    selected, missing_attempts = await missing_client.complete_json(
        replace(_listing_request(), required_listing_fields=("price", "district"))
    )

    assert selected.model == "preview"
    assert missing_transport.models == ["primary", "preview"]
    assert missing_attempts[0].decision_reason == "source_required_fields_missing:district"


@pytest.mark.asyncio
async def test_listing_policy_checks_required_fields_after_validator_normalization() -> None:
    transport = ScriptedTransport(
        [
            _listing_response(
                0.91,
                model="primary",
                is_rental_announcement=True,
                rental_confidence=0.9,
                price=450,
                phone_numbers=["not-a-phone"],
            ),
            _listing_response(
                0.92,
                model="preview",
                is_rental_announcement=True,
                rental_confidence=0.92,
                price=450,
                phone_numbers=["+998 90 123 45 67"],
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(
        replace(_listing_request(), required_listing_fields=("phone_numbers",))
    )

    assert response.model == "preview"
    assert transport.models == ["primary", "preview"]
    assert attempts[0].decision_reason == "source_required_fields_missing:phone_numbers"
    assert attempts[1].decision_reason == "listing_quality_gate_passed"


@pytest.mark.asyncio
async def test_listing_policy_ranks_required_completeness_before_other_signals() -> None:
    ops = RecordingOps()
    transport = ScriptedTransport(
        [
            _listing_response(
                0.61,
                model="primary",
                is_rental_announcement=True,
                rental_confidence=0.9,
                price=450,
                rooms=2,
            ),
            _listing_response(
                0.60,
                model="preview",
                listing_type="rent",
                rental_confidence=0.9,
                district="Yunusobod",
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=ops,
    )

    response, attempts = await client.complete_json(
        replace(_listing_request(), required_listing_fields=("district",))
    )

    assert response.model == "preview"
    assert attempts[1].selected is True
    assert attempts[1].selection_reason == "best_valid_low_quality_response_returned"
    assert attempts[1].fallback_trigger_reason == "source_required_fields_missing:district"
    assert any(
        "llm_selection" in message
        and "selected=true" in message
        and "selection_reason=best_valid_low_quality_response_returned" in message
        for message in ops.messages
    )


@pytest.mark.asyncio
async def test_listing_policy_rejects_contradictory_rental_classification_without_preview() -> None:
    transport = ScriptedTransport(
        [
            _listing_response(
                0.93,
                model="primary",
                is_rental_announcement=True,
                rental_confidence=0.95,
                listing_type="sale",
                price=85_000,
                rooms=3,
            ),
            _listing_response(
                0.94,
                model="preview",
                is_rental_announcement=True,
                listing_type="rent",
                price=600,
                rooms=3,
            ),
        ]
    )
    client = ModelFallbackLLMClient(
        models=("primary", "preview"),
        transport=transport,
        ops_notifier=RecordingOps(),
    )

    response, attempts = await client.complete_json(_listing_request())

    assert response.model == "primary"
    assert transport.models == ["primary"]
    assert attempts[0].status == "classification_conflict"
    assert attempts[0].decision_reason == "contradictory_rental_classification_no_fallback"
    assert attempts[0].selected is True


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
