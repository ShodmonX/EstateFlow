from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.source_config import InMemorySourceRegistry, SourceConfig
from services.ingestion_bootstrap.main import bootstrap_sources, parse_source_specs


def _settings(sources: list[dict[str, Any]]) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="test",
        ingestion_sources_json=json.dumps(sources),
    )


class FakeListenerFlagProvider:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    async def listener_sources_enabled(self) -> bool:
        return self.enabled


def _enabled_flags() -> FakeListenerFlagProvider:
    return FakeListenerFlagProvider()


def test_production_ingestion_role_does_not_require_unrelated_app_secrets() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="production",
        deployment_role="ingestion",
        queue_backend="rabbitmq",
        db_password=SecretStr("db-secret"),
        redis_password=SecretStr("redis-secret"),
        rabbitmq_url="amqp://pipeline:secret@rabbitmq:5672/",
        telegram_api_id=12345,
        telegram_api_hash=SecretStr("telegram-secret"),
        openrouter_api_key=SecretStr("ai-secret"),
        r2_endpoint_url="https://storage.example.test",
        r2_access_key_id=SecretStr("storage-key"),
        r2_secret_access_key=SecretStr("storage-secret"),
        r2_bucket="estateflow-media",
    )

    assert settings.deployment_role == "ingestion"


def test_parse_source_specs_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        parse_source_specs('[{"identifier":"@demo","name":"Demo","unknown":true}]')


@pytest.mark.asyncio
async def test_bootstrap_sources_is_idempotent() -> None:
    repository = InMemorySourceRegistry()
    settings = _settings(
        [
            {
                "source_type": "telegram_channel",
                "identifier": "@estate_demo",
                "name": "Estate demo",
                "adapter_name": "telegram_telethon",
                "source_profile": "default",
                "parser_key": "agency.bootstrap",
                "parser_version": "4",
                "parser_config": {
                    "family": "single_listing",
                    "score": {"minimum": 0.75},
                },
                "parser_mode": "shadow",
                "session_name": "acc_9889",
            }
        ]
    )

    first = await bootstrap_sources(settings, repository, _enabled_flags())
    second = await bootstrap_sources(settings, repository, _enabled_flags())

    assert first == {"configured": 1, "created": 1, "active_telegram_sources": 1}
    assert second == {"configured": 1, "created": 0, "active_telegram_sources": 1}
    source = (await repository.list_active_sources())[0]
    assert source.parser_key == "agency.bootstrap"
    assert source.parser_version == "4"
    assert source.parser_config == {
        "family": "single_listing",
        "score": {"minimum": 0.75},
    }
    assert source.parser_mode == "shadow"


@pytest.mark.asyncio
async def test_bootstrap_key_only_update_resets_stale_parser_recipe() -> None:
    repository = InMemorySourceRegistry(
        [
            SourceConfig(
                source_id="telegram_channel:@estate_demo",
                name="Estate demo",
                source_type="telegram_channel",
                identifier="@estate_demo",
                listener_account_key="acc_9889",
                parser_key="agency.old",
                parser_version="9",
                parser_config={"family": "digest_blocks"},
                parser_mode="shadow",
            )
        ]
    )
    settings = _settings(
        [
            {
                "identifier": "@estate_demo",
                "name": "Estate demo",
                "parser_key": "generic.mixed_feed",
                "session_name": "acc_9889",
            }
        ]
    )

    result = await bootstrap_sources(settings, repository, _enabled_flags())

    assert result == {"configured": 1, "created": 0, "active_telegram_sources": 1}
    source = (await repository.list_active_sources())[0]
    assert source.parser_key == "generic.mixed_feed"
    assert source.parser_version is None
    assert source.parser_config == {}
    assert source.parser_mode == "shadow"


@pytest.mark.parametrize(
    "parser_policy",
    [
        {"parser_key": "invalid key"},
        {"parser_mode": "observe"},
        {"parser_config": ["not", "an", "object"]},
        {
            "parser_key": "source.invalid",
            "parser_config": {"unknown_recipe_field": 0.8},
        },
    ],
)
def test_parse_source_specs_rejects_invalid_parser_policy(
    parser_policy: dict[str, object],
) -> None:
    payload = {"identifier": "@demo", "name": "Demo", **parser_policy}

    with pytest.raises(ValueError):
        parse_source_specs(json.dumps([payload]))


@pytest.mark.asyncio
async def test_bootstrap_requires_at_least_one_active_telegram_source() -> None:
    with pytest.raises(RuntimeError, match="No active Telegram ingestion source"):
        await bootstrap_sources(_settings([]), InMemorySourceRegistry(), _enabled_flags())


@pytest.mark.asyncio
async def test_bootstrap_requires_listener_feature_flag() -> None:
    settings = _settings([{"identifier": "@estate_demo", "name": "Estate demo"}])

    with pytest.raises(RuntimeError, match="listener_sources feature flag is disabled"):
        await bootstrap_sources(
            settings,
            InMemorySourceRegistry(),
            FakeListenerFlagProvider(enabled=False),
        )
