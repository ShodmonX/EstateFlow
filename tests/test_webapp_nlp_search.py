from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.application.runtime import EstateFlowRuntime
from estateflow.services.ai_client import LLMRequest, LLMResponse
from estateflow.services.nlp_search import NlpSearchExtractor
from estateflow.services.telegram_webapp_auth import TelegramWebAppIdentity


class FakeNlpLlm:
    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        return (
            LLMResponse(
                model="fake",
                content={
                    "monthly_budget": "600",
                    "price_basis_preference": "total",
                    "districts": ["Yunusobod"],
                    "rooms": 2,
                    "renovation_level": None,
                    "audience_tag": "yosh oila",
                    "unapplied_conditions": ["metroga yaqin"],
                    "confidence": 0.91,
                },
                confidence=0.91,
            ),
            [],
        )


class FakeWebAppAuth:
    def verify_session(self, token: str) -> TelegramWebAppIdentity:
        assert token == "test-token"
        return TelegramWebAppIdentity(user_id=42)


def _runtime(*, extractor: NlpSearchExtractor) -> EstateFlowRuntime:
    return EstateFlowRuntime(
        settings=Settings(
            environment="test",
            app_secret_key=SecretStr("secret"),
            telegram_bot_token=SecretStr("123456:token"),
        ),
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
        nlp_search_extractor=extractor,
    )


def test_webapp_nlp_search_returns_applied_and_unapplied_tags() -> None:
    app = create_app(
        Settings(
            environment="test",
            app_secret_key=SecretStr("secret"),
            telegram_bot_token=SecretStr("123456:token"),
        ),
        runtime=_runtime(extractor=NlpSearchExtractor(llm_client=FakeNlpLlm())),
    )
    app.state.webapp_auth_service = FakeWebAppAuth()

    response = TestClient(app).post(
        "/webapp/search/nlp",
        headers={"Authorization": "Bearer test-token"},
        json={"query": "Yunusobodda 2 xona, 600$ gacha, yosh oila uchun"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["criteria"]["district"] == "Yunusobod"
    assert data["criteria"]["max_price"] == "600"
    assert {tag["value"] for tag in data["tags"]} >= {
        "Yunusobod",
        "2 xona",
        "600 gacha",
        "Yosh oila",
        "metroga yaqin",
    }
    assert any(not tag["applied"] for tag in data["tags"])

