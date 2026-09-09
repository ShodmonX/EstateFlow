from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, cast

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.ops_notifications import OpsNotificationService

LLMRole = Literal["system", "user"]
LLMFallbackPolicy = Literal["confidence_only", "listing_quality"]
_ResponseQualityRank = tuple[int, int, int, int, float]


class LLMError(RuntimeError):
    pass


class LLMConfigurationError(LLMError):
    pass


class LLMTransportError(LLMError):
    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.retryable = retryable


class LLMMalformedResponseError(LLMError):
    pass


@dataclass(frozen=True)
class LLMMessage:
    role: LLMRole
    content: Any


@dataclass(frozen=True)
class LLMRequest:
    messages: list[LLMMessage]
    json_schema: dict[str, Any]
    correlation_id: str
    min_confidence: float
    temperature: float = 0.0
    fallback_policy: LLMFallbackPolicy = "confidence_only"
    required_listing_fields: tuple[str, ...] = ()
    response_validator: Callable[[dict[str, Any]], object] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class LLMResponse:
    model: str
    content: dict[str, Any]
    confidence: float
    usage: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None


@dataclass(frozen=True)
class LLMAttemptAudit:
    model: str
    fallback_stage: int
    latency_ms: int
    status: str
    error_type: str | None = None
    error_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None
    decision_reason: str | None = None
    selected: bool = False
    selection_reason: str | None = None
    fallback_trigger_reason: str | None = None


@dataclass(frozen=True)
class _ResponseFallbackDecision:
    should_retry: bool
    returnable: bool
    status: str
    reason: str
    quality_rank: _ResponseQualityRank


@dataclass(frozen=True)
class _ValidResponseCandidate:
    response: LLMResponse
    fallback_stage: int
    quality_rank: _ResponseQualityRank


class LLMAllModelsFailedError(LLMError):
    def __init__(self, attempts: list[LLMAttemptAudit]) -> None:
        super().__init__("All configured LLM models failed.")
        self.attempts = attempts


class LLMTransport(Protocol):
    async def complete_json(self, *, model: str, request: LLMRequest) -> LLMResponse: ...


class OpenRouterTransport:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        timeout_seconds: float,
        session: ClientSession | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = session

    async def complete_json(self, *, model: str, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [message.__dict__ for message in request.messages],
            "temperature": request.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "estateflow_listing", "schema": request.json_schema},
            },
        }
        headers = {
            "authorization": f"Bearer {self._api_key.get_secret_value()}",
            "content-type": "application/json",
            "x-title": "EstateFlow",
        }
        try:
            if self._session is not None:
                return await self._post(session=self._session, payload=payload, headers=headers)
            timeout = ClientTimeout(total=self._timeout_seconds)
            async with ClientSession(timeout=timeout) as session:
                return await self._post(session=session, payload=payload, headers=headers)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise LLMTransportError("OpenRouter timeout", retryable=True) from exc
        except ClientError as exc:
            raise LLMTransportError(type(exc).__name__, retryable=True) from exc

    async def _post(
        self,
        *,
        session: ClientSession,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> LLMResponse:
        async with session.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=headers,
        ) as response:
            if response.status in {408, 409, 425, 429} or response.status >= 500:
                detail = await _openrouter_error_detail(response)
                raise LLMTransportError(
                    f"OpenRouter HTTP {response.status}{detail}",
                    retryable=True,
                )
            if response.status >= 400:
                detail = await _openrouter_error_detail(response)
                raise LLMTransportError(
                    f"OpenRouter HTTP {response.status}{detail}",
                    retryable=False,
                )
            body = await response.json()
        return parse_openrouter_response(body)


class ModelFallbackLLMClient:
    def __init__(
        self,
        *,
        models: tuple[str, ...],
        transport: LLMTransport,
        ops_notifier: OpsNotificationService,
    ) -> None:
        if not models:
            raise LLMConfigurationError("At least one LLM model must be configured.")
        self._models = models
        self._transport = transport
        self._ops_notifier = ops_notifier

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[LLMAttemptAudit]]:
        attempts: list[LLMAttemptAudit] = []
        valid_candidates: list[_ValidResponseCandidate] = []
        for fallback_stage, model in enumerate(self._models):
            started = time.perf_counter()
            fallback_trigger_reason = (
                attempts[-1].decision_reason if fallback_stage > 0 and attempts else None
            )
            try:
                response = await self._transport.complete_json(model=model, request=request)
                latency_ms = _latency_ms(started)
                decision = _response_fallback_decision(request=request, response=response)
                attempt = LLMAttemptAudit(
                    model=model,
                    fallback_stage=fallback_stage,
                    latency_ms=latency_ms,
                    status=decision.status,
                    usage=response.usage,
                    provider_request_id=response.provider_request_id,
                    decision_reason=decision.reason,
                    selected=not decision.should_retry,
                    selection_reason=(
                        "response_accepted" if not decision.should_retry else None
                    ),
                    fallback_trigger_reason=fallback_trigger_reason,
                )
                attempts.append(attempt)
                await self._notify_attempt(request, attempt)
                if not decision.should_retry:
                    return response, attempts
                if decision.returnable:
                    valid_candidates.append(
                        _ValidResponseCandidate(
                            response=response,
                            fallback_stage=fallback_stage,
                            quality_rank=decision.quality_rank,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except LLMTransportError as exc:
                attempt = LLMAttemptAudit(
                    model=model,
                    fallback_stage=fallback_stage,
                    latency_ms=_latency_ms(started),
                    status="failed",
                    error_type=type(exc).__name__,
                    error_reason=str(exc),
                    decision_reason=(
                        "retryable_transport_error"
                        if exc.retryable
                        else "non_retryable_transport_error"
                    ),
                    fallback_trigger_reason=fallback_trigger_reason,
                )
                attempts.append(attempt)
                await self._notify_attempt(request, attempt)
                if not exc.retryable:
                    break
            except LLMError as exc:
                decision_reason = (
                    "malformed_response"
                    if isinstance(exc, LLMMalformedResponseError)
                    else "model_error"
                )
                attempt = LLMAttemptAudit(
                    model=model,
                    fallback_stage=fallback_stage,
                    latency_ms=_latency_ms(started),
                    status="failed",
                    error_type=type(exc).__name__,
                    error_reason=str(exc),
                    decision_reason=decision_reason,
                    fallback_trigger_reason=fallback_trigger_reason,
                )
                attempts.append(attempt)
                await self._notify_attempt(request, attempt)
        if valid_candidates:
            selected = max(valid_candidates, key=lambda item: item.quality_rank)
            attempts = _mark_selected_attempt(
                attempts,
                fallback_stage=selected.fallback_stage,
                selection_reason="best_valid_low_quality_response_returned",
            )
            selected_attempt = next(attempt for attempt in attempts if attempt.selected)
            await self._notify_selection(request, selected_attempt)
            return selected.response, attempts
        raise LLMAllModelsFailedError(attempts)

    async def _notify_attempt(self, request: LLMRequest, attempt: LLMAttemptAudit) -> None:
        await self._ops_notifier.notify(
            severity="info" if attempt.status == "success" else "warning",
            reason=(
                "llm_attempt "
                f"model={attempt.model} stage={attempt.fallback_stage} "
                f"status={attempt.status} latency_ms={attempt.latency_ms} "
                f"error_type={attempt.error_type or 'none'} "
                f"error_reason={attempt.error_reason or 'none'} "
                f"provider_request_id={attempt.provider_request_id or 'none'} "
                f"usage_keys={','.join(sorted(attempt.usage)) if attempt.usage else 'none'} "
                f"decision_reason={attempt.decision_reason or 'none'} "
                f"fallback_trigger_reason={attempt.fallback_trigger_reason or 'none'} "
                f"selected={str(attempt.selected).lower()} "
                f"selection_reason={attempt.selection_reason or 'none'}"
            ),
            correlation_id=request.correlation_id,
        )

    async def _notify_selection(self, request: LLMRequest, attempt: LLMAttemptAudit) -> None:
        await self._ops_notifier.notify(
            severity="warning",
            reason=(
                "llm_selection "
                f"model={attempt.model} stage={attempt.fallback_stage} "
                f"status={attempt.status} decision_reason={attempt.decision_reason or 'none'} "
                f"fallback_trigger_reason={attempt.fallback_trigger_reason or 'none'} "
                f"selected=true selection_reason={attempt.selection_reason or 'none'}"
            ),
            correlation_id=request.correlation_id,
        )


def _response_fallback_decision(
    *,
    request: LLMRequest,
    response: LLMResponse,
) -> _ResponseFallbackDecision:
    if request.fallback_policy == "listing_quality":
        return _listing_response_fallback_decision(request=request, response=response)
    if response.confidence >= request.min_confidence:
        return _ResponseFallbackDecision(
            should_retry=False,
            returnable=True,
            status="success",
            reason="confidence_threshold_met",
            quality_rank=(0, 0, 0, 0, response.confidence),
        )
    return _ResponseFallbackDecision(
        should_retry=True,
        returnable=False,
        status="low_confidence",
        reason="confidence_below_threshold",
        quality_rank=(0, 0, 0, 0, response.confidence),
    )


def _listing_response_fallback_decision(
    *,
    request: LLMRequest,
    response: LLMResponse,
) -> _ResponseFallbackDecision:
    content, schema_reason = _validated_response_content(request, response.content)
    if schema_reason is not None:
        return _ResponseFallbackDecision(
            should_retry=True,
            returnable=False,
            status="invalid_schema",
            reason=schema_reason,
            quality_rank=(0, 0, 0, 0, response.confidence),
        )

    is_rental = content.get("is_rental_announcement")
    listing_type = content.get("listing_type")
    core_signal_count = sum(
        1
        for field_name in _LISTING_CORE_SIGNAL_FIELDS
        if _has_meaningful_value(content.get(field_name))
    )
    missing_required_fields = tuple(
        field_name
        for field_name in request.required_listing_fields
        if not _has_meaningful_value(content.get(field_name))
    )
    required_present_count = len(request.required_listing_fields) - len(
        missing_required_fields
    )
    explicit_rental_rank = 2 if is_rental is True else 1 if listing_type == "rent" else 0
    quality_rank = (
        int(not missing_required_fields),
        required_present_count,
        explicit_rental_rank,
        core_signal_count,
        response.confidence,
    )
    status = "success" if response.confidence >= request.min_confidence else "low_confidence"

    if (is_rental is True and listing_type == "sale") or (
        is_rental is False and listing_type == "rent"
    ):
        return _ResponseFallbackDecision(
            should_retry=False,
            returnable=True,
            status="classification_conflict",
            reason="contradictory_rental_classification_no_fallback",
            quality_rank=quality_rank,
        )

    if is_rental is False or listing_type == "sale":
        return _ResponseFallbackDecision(
            should_retry=False,
            returnable=True,
            status=status,
            reason="explicit_non_rental_no_fallback",
            quality_rank=quality_rank,
        )

    plausible_listing = (
        is_rental is True or listing_type == "rent" or core_signal_count >= 2
    )
    if not plausible_listing:
        return _ResponseFallbackDecision(
            should_retry=False,
            returnable=True,
            status=status,
            reason="insufficient_listing_signals_no_fallback",
            quality_rank=quality_rank,
        )

    if missing_required_fields:
        return _ResponseFallbackDecision(
            should_retry=True,
            returnable=True,
            status="incomplete",
            reason="source_required_fields_missing:" + ",".join(missing_required_fields),
            quality_rank=quality_rank,
        )

    if core_signal_count < 2:
        return _ResponseFallbackDecision(
            should_retry=True,
            returnable=True,
            status="low_confidence" if status == "low_confidence" else "incomplete",
            reason="plausible_listing_incomplete",
            quality_rank=quality_rank,
        )

    if response.confidence < request.min_confidence:
        if request.required_listing_fields:
            return _ResponseFallbackDecision(
                should_retry=False,
                returnable=True,
                status="low_confidence",
                reason="source_required_fields_complete_no_fallback",
                quality_rank=quality_rank,
            )
        return _ResponseFallbackDecision(
            should_retry=True,
            returnable=True,
            status="low_confidence",
            reason="plausible_listing_low_confidence",
            quality_rank=quality_rank,
        )

    return _ResponseFallbackDecision(
        should_retry=False,
        returnable=True,
        status="success",
        reason="listing_quality_gate_passed",
        quality_rank=quality_rank,
    )


_LISTING_CORE_SIGNAL_FIELDS = (
    "price",
    "rooms",
    "area_sqm",
    "district",
    "address",
    "phone_numbers",
    "floor",
    "total_floors",
)


def _validated_response_content(
    request: LLMRequest,
    content: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    schema = request.json_schema
    required = schema.get("required")
    if isinstance(required, list) and any(
        isinstance(field_name, str) and field_name not in content for field_name in required
    ):
        return content, "schema_required_field_missing"

    properties = schema.get("properties")
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        if any(field_name not in properties for field_name in content):
            return content, "schema_unknown_field"

    if request.response_validator is not None:
        try:
            validated = request.response_validator(content)
            if isinstance(validated, dict):
                content = dict(validated)
            else:
                model_dump = getattr(validated, "model_dump", None)
                if callable(model_dump):
                    dumped = model_dump(mode="python")
                    if isinstance(dumped, dict):
                        content = dumped
        except Exception:
            return content, "schema_validation_failed"
    return content, None


def _has_meaningful_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _mark_selected_attempt(
    attempts: list[LLMAttemptAudit],
    *,
    fallback_stage: int,
    selection_reason: str,
) -> list[LLMAttemptAudit]:
    return [
        replace(
            attempt,
            selected=True,
            selection_reason=selection_reason,
        )
        if attempt.fallback_stage == fallback_stage
        else attempt
        for attempt in attempts
    ]


def parse_openrouter_response(body: dict[str, Any]) -> LLMResponse:
    try:
        choice = body["choices"][0]
        message = choice["message"]
        raw_content = message["content"]
        content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        if not isinstance(content, dict):
            raise TypeError("content is not an object")
        confidence = float(content.get("confidence", 0.0))
        raw_usage = body.get("usage")
        usage = cast(dict[str, Any], raw_usage) if isinstance(raw_usage, dict) else {}
        return LLMResponse(
            model=str(body.get("model") or ""),
            content=content,
            confidence=confidence,
            usage=usage,
            provider_request_id=body.get("id") if isinstance(body.get("id"), str) else None,
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        request_id = body.get("id") if isinstance(body.get("id"), str) else "unknown"
        model = body.get("model") if isinstance(body.get("model"), str) else "unknown"
        raise LLMMalformedResponseError(
            f"Malformed OpenRouter response id={request_id} model={model}"
        ) from exc


async def _openrouter_error_detail(response: Any) -> str:
    """Return a short provider error without leaking request credentials."""
    try:
        body = await response.json(content_type=None)
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if message:
                return f": {str(message)[:240]}"
    except Exception:
        pass
    return ""


def create_openrouter_llm_client(
    *,
    settings: Settings,
    ops_notifier: OpsNotificationService,
    session: ClientSession | None = None,
) -> ModelFallbackLLMClient:
    if settings.openrouter_api_key is None:
        raise LLMConfigurationError("OPENROUTER_API_KEY is required for OpenRouter LLM client.")
    return ModelFallbackLLMClient(
        models=settings.openrouter_models,
        transport=OpenRouterTransport(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.openrouter_timeout_seconds,
            session=session,
        ),
        ops_notifier=ops_notifier,
    )


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
