from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg2  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.admin import SourceSuggestionSubmitRequest
from estateflow.api.app import create_app
from estateflow.application.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def test_default_app_search_endpoint_should_be_available_without_manual_wiring() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)

    response = client.get("/search/announcements")

    assert response.status_code == 200


def test_migration_smoke_should_not_seed_is_promoted_as_null() -> None:
    python_smoke = Path("scripts/migration_smoke.py").read_text(encoding="utf-8").lower()
    psql_smoke = Path("scripts/migration_smoke.psql").read_text(encoding="utf-8").lower()

    assert "now(), null" not in python_smoke
    assert "now(), null" not in psql_smoke


@pytest.mark.integration
@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_feature_flags_should_survive_restart() -> None:
    _require_docker()
    postgres_name = f"estateflow-pg-flags-{uuid4().hex[:10]}"
    postgres_port: int | None = None
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                postgres_name,
                "-e",
                "POSTGRES_PASSWORD=postgres",
                "-e",
                "POSTGRES_DB=estateflow_test",
                "-p",
                "127.0.0.1::5432",
                "postgres:16-alpine",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        postgres_port = _resolve_container_port(postgres_name, 5432)
        _wait_for_postgres("127.0.0.1", postgres_port, "estateflow_test", "postgres", "postgres")

        settings = Settings(
            environment="test",
            db_host="127.0.0.1",
            db_port=postgres_port,
            db_name="estateflow_test",
            db_user="postgres",
            db_password=SecretStr("postgres"),
            redis_password=None,
            queue_backend="redis",
            admin_api_token=SecretStr("secret-admin"),
        )
        env = _runtime_env(settings)
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            check=True,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        first_app = create_app(settings)
        second_app = create_app(settings)
        try:
            await first_app.state.release_controls.set_flag(
                name="notifications",
                enabled=True,
                expected_version=1,
                actor="release-engineer",
                reason="persist runtime flag across restart",
            )
            flags = await second_app.state.release_controls.flags()

            assert flags.notifications is True
        finally:
            await first_app.state.runtime.aclose()
            await second_app.state.runtime.aclose()
    finally:
        if postgres_port is not None:
            subprocess.run(
                ["docker", "rm", "-f", postgres_name],
                check=False,
                capture_output=True,
                text=True,
            )


def _require_docker() -> None:
    try:
        subprocess.run(["docker", "version"], check=True, capture_output=True, text=True)
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Docker daemon unavailable for runtime integration smoke: {exc}")


def _resolve_container_port(container_name: str, internal_port: int) -> int:
    deadline = time.time() + 30
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "port", container_name, str(internal_port)],
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        if output:
            return int(output.rsplit(":", maxsplit=1)[-1])
        time.sleep(0.5)
    raise TimeoutError(f"docker port never resolved for {container_name}")


def _wait_for_postgres(host: str, port: int, dbname: str, user: str, password: str) -> None:
    deadline = time.time() + 60
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=dbname,
                user=user,
                password=password,
            )
            conn.close()
            return
        except Exception as exc:  # pragma: no cover - timing loop
            last_error = exc
            time.sleep(0.5)
    raise TimeoutError("postgres never became ready") from last_error


def _runtime_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": settings.environment,
            "DB_HOST": settings.db_host,
            "DB_PORT": str(settings.db_port),
            "DB_NAME": settings.db_name,
            "DB_USER": settings.db_user,
            "DB_PASSWORD": settings.db_password.get_secret_value() if settings.db_password else "",
        }
    )
    return env


def test_source_suggestion_submit_request_should_not_expose_user_id_body_field() -> None:
    assert "user_id" not in SourceSuggestionSubmitRequest.model_fields
