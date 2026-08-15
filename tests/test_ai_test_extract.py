from __future__ import annotations

# mypy: disable-error-code="arg-type"
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.application.runtime import EstateFlowRuntime
from estateflow.services.ai_client import LLMRequest, LLMResponse


class DummyTransport:
    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        return (
            LLMResponse(
                model="google/gemini-3.1-flash-lite",
                content={
                    "price": 350.0,
                    "currency": "USD",
                    "price_period": "monthly",
                    "price_basis": "total",
                    "district": "Yashnobod",
                    "rooms": 2,
                    "description": "Yashnobod 2 xonali kvartira 350$",
                    "confidence": 0.95,
                },
                confidence=0.95,
            ),
            [],
        )


@pytest.mark.asyncio
async def test_ai_test_extract_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        environment="test",
        admin_api_token=SecretStr("secret-admin"),
        openrouter_api_key=SecretStr("test-openrouter-key"),
    )

    runtime = EstateFlowRuntime(
        settings=settings,
        db=None,
        redis=None,
        source_registry=None,
        listener_refresh_store=None,
        adapter_registry=None,
        listener_coordinator=None,
        listener_topology_service=None,
        telegram_session_inventory_service=None,
        health_checker=None,
        release_controls=None,
        ops_notifier=None,
        analytics_recorder=None,
        technical_recorder=None,
        website_scraper_service=None,
        metrics_service=None,
        search_service=None,
        saved_filter_service=None,
        source_suggestion_service=None,
        telegram_auth_service=None,
        audience_tag_service=None,
        content_automation_service=None,
        user_service=None,
        manual_review_queue=None,
        post_ai_dedup_processor=None,
        notification_matching_engine=None,
        notification_delivery_repository=None,
        notification_bot=None,
        bot_controller=None,
        queue_consumer=None,
    )
    app = create_app(settings, runtime=runtime)

    from estateflow.api import admin

    monkeypatch.setattr(
        admin,
        "create_openrouter_llm_client",
        lambda settings, ops_notifier: type(
            "DummyClient",
            (),
            {"complete_json": DummyTransport().complete_json},
        )(),
    )

    client = TestClient(app)
    response = client.post(
        "/admin/ai/test-extract",
        headers={"x-admin-token": "secret-admin"},
        data={"text": "Район: Яшнобад -Кол.Комнат:2 -Цена: 350у.е."},
        files=[("file1", ("test.jpg", b"\xff\xd8\xff\xe0testimagebytes", "image/jpeg"))],
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["media_count"] == 1
    assert data["canonical"]["district"] == "Yashnobod"
    assert data["canonical"]["rooms"] == 2
    assert float(data["canonical"]["price"]) == 350.0


def test_ai_test_extract_endpoint_requires_admin_token() -> None:
    settings = Settings(
        environment="test",
        admin_api_token=SecretStr("secret-admin"),
        openrouter_api_key=SecretStr("test-openrouter-key"),
    )
    runtime = EstateFlowRuntime(
        settings=settings,
        db=None,
        redis=None,
        source_registry=None,
        listener_refresh_store=None,
        adapter_registry=None,
        listener_coordinator=None,
        listener_topology_service=None,
        telegram_session_inventory_service=None,
        health_checker=None,
        release_controls=None,
        ops_notifier=None,
        analytics_recorder=None,
        technical_recorder=None,
        website_scraper_service=None,
        metrics_service=None,
        search_service=None,
        saved_filter_service=None,
        source_suggestion_service=None,
        telegram_auth_service=None,
        audience_tag_service=None,
        content_automation_service=None,
        user_service=None,
        manual_review_queue=None,
        post_ai_dedup_processor=None,
        notification_matching_engine=None,
        notification_delivery_repository=None,
        notification_bot=None,
        bot_controller=None,
        queue_consumer=None,
    )
    client = TestClient(create_app(settings, runtime=runtime))

    response = client.post("/admin/ai/test-extract", data={"text": "listing"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_unauthorized"
