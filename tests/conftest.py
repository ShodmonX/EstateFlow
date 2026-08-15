from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import FROZEN_NOW, TelegramPostFixture, representative_posts

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FLOW_MARKERS_BY_FILE: dict[str, tuple[str, ...]] = {
    "test_adapter_registry.py": ("unit", "integration"),
    "test_admin_sources_enable.py": ("integration", "admin_review"),
    "test_admin_telegram_auth.py": ("integration", "admin_review"),
    "test_ai_test_extract.py": ("integration", "ai_extraction"),
    "test_ingestion_bootstrap.py": ("unit", "ingestion"),
    "test_app.py": ("unit", "observability"),
    "test_observability.py": ("unit", "observability"),
    "test_listener_pool.py": ("unit", "ingestion"),
    "test_telegram_listener.py": ("unit", "ingestion"),
    "test_media_buffer_queue.py": ("integration", "ingestion"),
    "test_pre_ai_dedup.py": ("unit", "pre_ai_dedup"),
    "test_sprint1_integration.py": ("integration", "ingestion", "pre_ai_dedup"),
    "test_sprint2_ai_client.py": ("unit", "ai_extraction"),
    "test_sprint2_extraction.py": ("unit", "ai_extraction"),
    "test_sprint2_media_and_worker.py": ("integration", "ai_extraction", "media_r2"),
    "test_post_ai_dedup.py": ("unit", "post_ai_dedup"),
    "test_sprint3_final_flow.py": ("e2e", "ingestion", "ai_extraction", "post_ai_dedup"),
    "test_sprint3_migration.py": ("unit", "migrations"),
    "test_sprint4_search.py": ("unit", "search"),
    "test_sprint4_saved_filters_and_bot.py": ("integration", "search", "saved_filters"),
    "test_webapp_auth.py": ("unit", "search"),
    "test_rabbitmq_queue.py": ("integration", "observability"),
    "test_sprint5_notifications.py": ("unit", "notification"),
    "test_sprint5_notification_delivery.py": ("integration", "notification"),
    "test_sprint5_nlp_search.py": ("integration", "nlp"),
    "test_sprint5_referrals.py": ("unit", "referral"),
    "test_sprint5_e2e.py": ("e2e", "saved_filters", "notification", "nlp", "referral"),
    "test_sprint6_admin_tags_content.py": (
        "integration",
        "admin_review",
        "content_scheduling",
    ),
    "test_sprint6_content_pipeline.py": ("integration", "content_scheduling"),
    "test_sprint6_integration_handoff.py": (
        "e2e",
        "admin_review",
        "content_scheduling",
    ),
    "test_sprint7_metrics.py": ("integration", "metrics", "notification"),
    "test_sprint7_release_readiness.py": (
        "integration",
        "observability",
        "test_foundation",
    ),
    "test_sprint8_alembic_integration.py": ("integration", "migrations"),
    "test_sprint8_review_red.py": ("integration", "test_foundation"),
    "test_runtime_integration.py": ("integration", "observability"),
    "test_runtime_notification_wiring.py": ("integration", "notification"),
    "test_notification_payload.py": ("unit", "notification"),
    "test_sprint7_test_foundation.py": ("unit", "test_foundation"),
    "test_sprint7_e2e_regression.py": (
        "e2e",
        "ingestion",
        "pre_ai_dedup",
        "ai_extraction",
        "media_r2",
        "post_ai_dedup",
        "search",
        "saved_filters",
        "notification",
        "nlp",
        "referral",
        "admin_review",
    ),
    "test_website_scraper.py": ("integration", "content_scheduling"),
}


def pytest_configure(config: pytest.Config) -> None:
    markers = {
        "unit": "Fast deterministic unit tests with no external services.",
        "integration": "Local integration tests using in-memory or fake dependencies.",
        "e2e": "End-to-end tests that run a full fake stack.",
        "allow_network": "Allow network sockets for explicit local container smoke tests.",
        "ingestion": "Telegram listener, listener pool, media buffer, raw queue flow.",
        "pre_ai_dedup": "Pre-AI deduplication decision flow.",
        "ai_extraction": "AI extraction, prompt, validation, and worker flow.",
        "media_r2": "Media processing and R2-compatible storage abstraction.",
        "post_ai_dedup": "Post-AI deduplication, parent-child merge, manual review.",
        "search": "Search service and API semantics.",
        "saved_filters": "Saved filter CRUD and bot flow.",
        "notification": "Notification matching, delivery, and engagement callbacks.",
        "nlp": "Natural-language search extraction and bot fallback.",
        "referral": "Referral activation and Premium milestone flow.",
        "admin_review": "Admin review/source suggestion/audience tag flows.",
        "content_scheduling": "Content automation, dry-run, publisher, and caps.",
        "metrics": "Beta product metrics and analytics events.",
        "migrations": "Migration contract and smoke coverage.",
        "observability": "Health, logging, redaction, and Ops notification coverage.",
        "test_foundation": "Test fixture, marker, and environment contract checks.",
    }
    for name, description in markers.items():
        config.addinivalue_line("markers", f"{name}: {description}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        markers = FLOW_MARKERS_BY_FILE.get(Path(str(item.fspath)).name, ("unit",))
        for marker in markers:
            item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture(autouse=True)
def deterministic_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPS_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture(autouse=True)
def block_external_network(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("allow_network"):
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        _reject_external_address(address)
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        _reject_external_address(address)
        return original_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture
def frozen_now() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def telegram_post_fixtures() -> tuple[TelegramPostFixture, ...]:
    return representative_posts()


@pytest.fixture
def isolated_tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _reject_external_address(address: Any) -> None:
    host = address[0] if isinstance(address, tuple) and address else address
    if host in {"127.0.0.1", "::1", "localhost"}:
        return
    if isinstance(host, str) and host.startswith("0.0.0.0"):
        return
    raise RuntimeError(f"External network is disabled in tests: {host}")
