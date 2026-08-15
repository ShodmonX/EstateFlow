from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.application.core.redaction import REDACTED, redact, redact_text
from estateflow.services.health import DependencyStatus
from estateflow.services.ops_notifications import (
    DisabledOpsNotificationService,
    TelegramOpsNotificationService,
    create_ops_notification_service,
    humanize_ops_reason,
)


class StubHealthChecker:
    def __init__(self, statuses: list[DependencyStatus]) -> None:
        self._statuses = statuses

    async def check(self) -> list[DependencyStatus]:
        return self._statuses


class RecordingOpsNotificationService(TelegramOpsNotificationService):
    def __init__(self) -> None:
        super().__init__(
            bot_token=SecretStr("123456789:abcdefghijklmnopqrstuvwxyzABCDE"),
            chat_id="-100123",
            rate_limit_window_seconds=300,
        )
        self.messages: list[str] = []

    async def _send_message(self, text: str) -> None:
        self.messages.append(text)


class RateLimitedOpsNotificationService(TelegramOpsNotificationService):
    def __init__(self) -> None:
        super().__init__(
            bot_token=SecretStr("123456789:abcdefghijklmnopqrstuvwxyzABCDE"),
            chat_id="-100123",
        )
        self.calls = 0

    async def _send_message(self, text: str) -> tuple[int, int | None]:
        self.calls += 1
        return 429, 60


def test_correlation_id_header_is_reused_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(
        Settings(environment="test"),
        health_checker=StubHealthChecker([]),
        ops_notifier=DisabledOpsNotificationService(),
    )
    correlation_id = "test-correlation-id"

    with caplog.at_level(logging.INFO):
        response = TestClient(app).get(
            "/health/live",
            headers={"x-correlation-id": correlation_id},
        )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == correlation_id
    request_logs = [
        record
        for record in caplog.records
        if getattr(record, "event", "").startswith("http.request.")
    ]
    assert request_logs
    assert {cast(Any, record).correlation_id for record in request_logs} == {correlation_id}


def test_redaction_masks_sensitive_values() -> None:
    assert REDACTED in redact_text("+998 90 123 45 67")
    assert REDACTED in redact_text("123456789:abcdefghijklmnopqrstuvwxyzABCDE")
    assert redact(
        {
            "db_password": "secret",
            "raw_text": "full listing body",
            "nested": {"phone": "+998901234567"},
        }
    ) == {
        "db_password": REDACTED,
        "raw_text": REDACTED,
        "nested": {"phone": REDACTED},
    }


def test_liveness_does_not_require_dependencies() -> None:
    app = create_app(
        Settings(environment="test"),
        health_checker=StubHealthChecker(
            [DependencyStatus(name="postgres", status="error", error="TimeoutError")]
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_200_when_dependencies_are_ok() -> None:
    app = create_app(
        Settings(environment="test"),
        health_checker=StubHealthChecker(
            [
                DependencyStatus(name="postgres", status="ok"),
                DependencyStatus(name="redis", status="ok"),
            ]
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_returns_503_without_leaking_secrets() -> None:
    app = create_app(
        Settings(
            environment="test",
            db_password=SecretStr("super-secret-db-password"),
            redis_password=SecretStr("super-secret-redis-password"),
        ),
        health_checker=StubHealthChecker(
            [
                DependencyStatus(name="postgres", status="error", error="TimeoutError"),
                DependencyStatus(name="redis", status="ok"),
            ]
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    response = TestClient(app).get("/health/ready")
    response_text = response.text

    assert response.status_code == 503
    assert "TimeoutError" in response_text
    assert "super-secret-db-password" not in response_text
    assert "super-secret-redis-password" not in response_text


def test_ops_notifier_is_disabled_without_ops_credentials() -> None:
    service = create_ops_notification_service(ops_bot_token=None, ops_chat_id=None)

    assert service.enabled is False


@pytest.mark.asyncio
async def test_ops_notifier_deduplicates_repeated_alerts() -> None:
    service = RecordingOpsNotificationService()

    first_sent = await service.notify(
        severity="critical",
        reason="AI failed for +998901234567 with token 123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        correlation_id="cid-1",
    )
    second_sent = await service.notify(
        severity="critical",
        reason="AI failed for +998901234567 with token 123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        correlation_id="cid-2",
    )

    assert first_sent is True
    assert second_sent is False
    assert len(service.messages) == 1
    assert "+998901234567" not in service.messages[0]
    assert "123456789:abcdefghijklmnopqrstuvwxyzABCDE" not in service.messages[0]
    assert REDACTED in service.messages[0]


def test_ops_message_uses_human_readable_uzbek_text() -> None:
    message = humanize_ops_reason(
        "llm_attempt model=google/gemini-3.1-flash-lite stage=0 "
        "status=low_confidence latency_ms=1775 provider_request_id=none usage_keys=total_tokens"
    )

    assert message == (
        "AI modeli google/gemini-3.1-flash-lite ishonch darajasi past javob berdi. "
        "Zaxira bosqichi: 0. Javob vaqti: 1775 ms."
    )
    assert "llm_attempt" not in message
    assert "status=" not in message


@pytest.mark.asyncio
async def test_ops_message_has_readable_header_and_keeps_correlation_id() -> None:
    service = RecordingOpsNotificationService()

    sent = await service.notify(
        severity="critical",
        reason="ai_processing failed source=telegram_channel:-1001 error=LLMAllModelsFailedError",
        correlation_id="cid-human-readable",
    )

    assert sent is True
    assert service.messages[0].startswith("EstateFlow OPS\nHolat: Jiddiy xatolik\nXabar:")
    assert "E’lonni AI qayta ishlay olmadi" in service.messages[0]
    assert "Tekshiruv ID: cid-human-readable" in service.messages[0]


@pytest.mark.asyncio
async def test_ops_notifier_429_is_best_effort_and_enters_cooldown() -> None:
    service = RateLimitedOpsNotificationService()

    first_sent = await service.notify(
        severity="warning",
        reason="first alert",
        correlation_id="cid-1",
    )
    second_sent = await service.notify(
        severity="warning",
        reason="different alert",
        correlation_id="cid-2",
    )

    assert first_sent is False
    assert second_sent is False
    assert service.calls == 1
