from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.ops_notifications import OpsNotificationService

LLMRole = Literal["system", "user"]


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
        for fallback_stage, model in enumerate(self._models):
            started = time.perf_counter()
            try:
                response = await self._transport.complete_json(model=model, request=request)
                latency_ms = _latency_ms(started)
                status = "success"
                if response.confidence < request.min_confidence:
                    status = "low_confidence"
                attempts.append(
                    LLMAttemptAudit(
                        model=model,
                        fallback_stage=fallback_stage,
                        latency_ms=latency_ms,
                        status=status,
                        usage=response.usage,
                        provider_request_id=response.provider_request_id,
                    )
                )
                await self._notify_attempt(request, attempts[-1])
                if response.confidence >= request.min_confidence:
                    return response, attempts
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
                )
                attempts.append(attempt)
                await self._notify_attempt(request, attempt)
                if not exc.retryable:
                    break
            except LLMError as exc:
                attempt = LLMAttemptAudit(
                    model=model,
                    fallback_stage=fallback_stage,
                    latency_ms=_latency_ms(started),
                    status="failed",
                    error_type=type(exc).__name__,
                    error_reason=str(exc),
                )
                attempts.append(attempt)
                await self._notify_attempt(request, attempt)
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
                f"usage_keys={','.join(sorted(attempt.usage)) if attempt.usage else 'none'}"
            ),
            correlation_id=request.correlation_id,
        )


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
