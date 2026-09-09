from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import ApiProfile, create_app
from estateflow.application.core.config import Settings


def test_settings_ignore_ambient_generic_debug_variable(monkeypatch) -> None:
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.delenv("ESTATEFLOW_DEBUG", raising=False)

    settings = Settings(environment="test")

    assert settings.debug is False


def test_settings_read_namespaced_debug_variable(monkeypatch) -> None:
    monkeypatch.setenv("ESTATEFLOW_DEBUG", "true")

    settings = Settings(environment="test")

    assert settings.debug is True


def test_create_app_import_safe() -> None:
    app = create_app(Settings(environment="test"))

    assert app.title == "EstateFlow API"


def test_api_profiles_expose_only_their_owned_routes() -> None:
    settings = Settings(environment="test")
    def paths(profile: ApiProfile) -> set[str]:
        app = create_app(settings, profile=profile)
        result: set[str] = set()
        for route in app.routes:
            if hasattr(route, "path"):
                result.add(route.path)
            else:
                result.update(
                    nested.path
                    for nested in getattr(getattr(route, "original_router", None), "routes", ())
                    if hasattr(nested, "path")
                )
        return result

    public_paths = paths("public")
    admin_paths = paths("admin")
    internal_paths = paths("internal")

    assert "/search/announcements" in public_paths
    assert "/webapp/me" in public_paths
    assert "/admin/release/flags" not in public_paths
    assert "/admin/release/flags" in admin_paths
    assert "/search/announcements" not in admin_paths
    assert "/internal/source-suggestions" in internal_paths
    assert "/admin/release/flags" not in internal_paths


def test_liveness_is_independent_from_external_services() -> None:
    app = create_app(
        Settings(
            environment="test",
            db_host="localhost",
            db_port=1,
            redis_host="localhost",
            redis_port=1,
        )
    )

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_builds_database_and_redis_urls_from_parts() -> None:
    settings = Settings(
        environment="test",
        db_host="postgres",
        db_port=5433,
        db_name="estateflow_test",
        db_user="estateflow_user",
        db_password=SecretStr("secret"),
        redis_host="redis",
        redis_port=6380,
        redis_db=2,
        redis_password=SecretStr("redis_secret"),
    )

    assert settings.database_url == (
        "postgresql+asyncpg://estateflow_user:secret@postgres:5433/estateflow_test"
    )
    assert settings.postgres_url == (
        "postgresql+psycopg2://estateflow_user:secret@postgres:5433/estateflow_test"
    )
    assert settings.sync_database_url == (
        "postgresql+psycopg2://estateflow_user:secret@postgres:5433/estateflow_test"
    )
    assert settings.redis_url == "redis://:redis_secret@redis:6380/2"
