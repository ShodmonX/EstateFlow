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

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


@pytest.mark.integration
@pytest.mark.allow_network
def test_real_runtime_boots_on_throwaway_postgres_and_redis() -> None:
    _require_docker()
    postgres_name = f"estateflow-pg-{uuid4().hex[:10]}"
    redis_name = f"estateflow-redis-{uuid4().hex[:10]}"
    postgres_port: int | None = None
    redis_port: int | None = None
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
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                redis_name,
                "-p",
                "127.0.0.1::6379",
                "redis:7-alpine",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        postgres_port = _resolve_container_port(postgres_name, 5432)
        redis_port = _resolve_container_port(redis_name, 6379)
        _wait_for_postgres("127.0.0.1", postgres_port, "estateflow_test", "postgres", "postgres")
        _wait_for_redis(redis_name)

        settings = Settings(
            environment="test",
            db_host="127.0.0.1",
            db_port=postgres_port,
            db_name="estateflow_test",
            db_user="postgres",
            db_password=SecretStr("postgres"),
            redis_host="127.0.0.1",
            redis_port=redis_port,
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

        app = create_app(settings)
        runtime = app.state.runtime
        with TestClient(app) as client:
            search = client.get("/search/announcements")
            metrics = client.get("/admin/metrics/beta", headers={"X-Admin-Token": "secret-admin"})
            content = client.post(
                "/admin/content/daily",
                headers={"X-Admin-Token": "secret-admin"},
            )
            health = client.get("/health/ready")

            assert search.status_code == 200
            assert search.json()["items"] == []
            assert metrics.status_code == 200
            assert metrics.json()["technical_metrics"] is not None
            assert content.status_code == 200
            assert isinstance(content.json(), list)
            assert health.status_code == 200

        assert runtime.redis is not None

    finally:
        if postgres_port is not None:
            subprocess.run(
                ["docker", "rm", "-f", postgres_name],
                check=False,
                capture_output=True,
                text=True,
            )
        if redis_port is not None:
            subprocess.run(
                ["docker", "rm", "-f", redis_name],
                check=False,
                capture_output=True,
                text=True,
            )


@pytest.mark.integration
@pytest.mark.allow_network
def test_release_flags_persist_and_reject_stale_updates_across_app_instances() -> None:
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
            admin_actor_user_id=7,
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
        with TestClient(first_app) as client1, TestClient(second_app) as client2:
            current = client1.get("/admin/release/flags", headers={"X-Admin-Token": "secret-admin"})
            expected_version = current.json()["versions"]["notifications"]
            changed = client1.post(
                "/admin/release/flags",
                headers={"X-Admin-Token": "secret-admin"},
                json={
                    "flag_name": "notifications",
                    "enabled": True,
                    "expected_version": expected_version,
                    "reason": "enable notifications across processes",
                },
            )
            stale = client2.post(
                "/admin/release/flags",
                headers={"X-Admin-Token": "secret-admin"},
                json={
                    "flag_name": "notifications",
                    "enabled": False,
                    "expected_version": expected_version,
                    "reason": "stale overwrite attempt",
                },
            )
            refreshed = client2.get(
                "/admin/release/flags",
                headers={"X-Admin-Token": "secret-admin"},
            )

        assert changed.status_code == 200
        assert stale.status_code == 409
        assert refreshed.json()["flags"]["notifications"] is True
        assert refreshed.json()["versions"]["notifications"] == expected_version + 1
        assert len(refreshed.json()["audit_events"]) == 1
        assert refreshed.json()["audit_events"][0]["actor"] == "server-admin:7"
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
        subprocess.run(
            ["docker", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
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


def _wait_for_redis(container_name: str) -> None:
    deadline = time.time() + 60
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["docker", "exec", container_name, "redis-cli", "ping"],
                check=True,
                capture_output=True,
                text=True,
            )
            if "PONG" in result.stdout:
                return
        except Exception as exc:  # pragma: no cover - timing loop
            last_error = exc
            time.sleep(0.5)
    raise TimeoutError("redis never became ready") from last_error


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
            "REDIS_HOST": settings.redis_host,
            "REDIS_PORT": str(settings.redis_port),
        }
    )
    return env
