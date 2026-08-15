from __future__ import annotations

import asyncio
from pathlib import Path

from estateflow.adapters.registry import AdapterRegistry
from estateflow.application.core.config import Settings, get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.repositories.release_controls import SQLAlchemyFeatureFlagStore
from estateflow.repositories.sqlalchemy import SQLAlchemySourceConfigRepository
from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
from estateflow.services.listener_refresh import (
    RedisListenerRefreshStore,
    TelegramListenerCoordinator,
)
from estateflow.services.listener_status import RedisListenerTopologyStore
from estateflow.services.release_controls import (
    FeatureGatedSourceProvider,
    ReleaseControlService,
    beta_policy_from_settings,
    feature_flags_from_settings,
)
from services.common.runtime import create_ops_notifier, create_resources


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    resources = create_resources(settings)
    try:
        adapter_registry = AdapterRegistry(_adapter_plugin_dir(settings), settings)
        adapter_registry.reload_sync()
        source_registry = SQLAlchemySourceConfigRepository(resources.db.session)
        release_controls = ReleaseControlService(
            store=SQLAlchemyFeatureFlagStore(
                resources.db.session,
                initial=feature_flags_from_settings(settings),
            ),
            cohort_policy=beta_policy_from_settings(settings),
        )
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=adapter_registry,
            source_provider=FeatureGatedSourceProvider(
                source_registry,
                release_controls=release_controls,
            ),
            refresh_store=RedisListenerRefreshStore(resources.redis),
            redis=resources.redis,
            ops_notifier=create_ops_notifier(settings),
            event_queue=resources.queue,
            topology_store=RedisListenerTopologyStore(resources.redis),
            listener_account_key=settings.default_telegram_listener_account_key,
            default_adapter_name=DEFAULT_TELEGRAM_ADAPTER_NAME,
        )
        try:
            await coordinator.run_forever()
        finally:
            await coordinator.aclose()
    finally:
        await resources.aclose()


def _adapter_plugin_dir(settings: Settings) -> Path:
    if settings.adapter_plugin_dir:
        return Path(settings.adapter_plugin_dir)
    return Path(__file__).resolve().parents[2] / "src" / "estateflow" / "adapters" / "plugins"


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
