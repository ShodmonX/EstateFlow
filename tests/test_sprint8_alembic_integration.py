from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg2  # type: ignore[import-untyped]
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, text

from estateflow.application.core.config import Settings
from estateflow.db.schema_inspection import compare_schema
from estateflow.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
LEGACY_MIGRATIONS = tuple(
    PROJECT_ROOT / "migrations" / name
    for name in (
        "001_listener_pool.sql",
        "002_mvp_schema_and_post_ai_dedup.sql",
        "003_sprint3_schema_contract_adjustments.sql",
        "004_sprint4_search_and_saved_filters.sql",
        "005_sprint5_notification_matching.sql",
        "006_sprint5_notification_delivery.sql",
        "007_sprint5_referral_premium.sql",
        "008_sprint6_admin_tags_content.sql",
        "009_sprint7_analytics_events.sql",
        "010_sprint7_technical_metric_events.sql",
        "011_media_per_announcement.sql",
    )
)


@pytest.mark.migrations
def test_alembic_upgrade_head_and_bridge_stamp_on_throwaway_postgres() -> None:
    _require_docker()
    container_name = f"estateflow-alembic-{uuid4().hex[:12]}"
    postgres_password = "postgres"
    postgres_db = "estateflow_test"
    host_port: int | None = None
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                container_name,
                "-e",
                f"POSTGRES_PASSWORD={postgres_password}",
                "-e",
                f"POSTGRES_DB={postgres_db}",
                "-p",
                "127.0.0.1::5432",
                "postgres:16-alpine",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        host_port = _resolve_container_port(container_name)
        _wait_for_postgres("127.0.0.1", host_port, postgres_db, "postgres", postgres_password)

        settings = Settings(
            environment="test",
            db_host="127.0.0.1",
            db_port=host_port,
            db_name=postgres_db,
            db_user="postgres",
            db_password=SecretStr(postgres_password),
        )
        env = _alembic_env(settings)

        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            check=True,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            check=True,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        current_run = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "current"],
            check=True,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        engine = create_engine(settings.sync_database_url)
        try:
            inspector = inspect(engine)
            observed_tables = set(inspector.get_table_names())
            assert set(Base.metadata.tables).issubset(observed_tables)
            assert "alembic_version" in observed_tables

            for table_name, column_name in (
                ("announcements", "is_promoted"),
                ("notifications", "next_attempt_at"),
                ("source_suggestions", "source_type"),
                ("technical_metric_events", "metric_name"),
            ):
                columns = {column["name"] for column in inspector.get_columns(table_name)}
                assert column_name in columns

            index_names = {
                index["name"]
                for table_name in (
                    "announcements",
                    "notifications",
                    "source_suggestions",
                    "analytics_events",
                )
                for index in inspector.get_indexes(table_name)
            }
            assert "ix_announcements_user_search_canonical" in index_names
            assert "ix_notifications_ready_delivery" in index_names
            assert "ix_source_suggestions_pending" in index_names
            assert "ix_analytics_events_name_time" in index_names

            with engine.begin() as connection:
                connection.execute(
                    text("insert into listener_accounts(account_key) values ('listener-a')")
                )
                connection.execute(
                    text(
                        """
                        insert into ingestion_sources(
                            source_id, name, source_type, identifier, listener_account_key
                        )
                        values ('src-a', 'A', 'telegram_channel', '@a', 'listener-a')
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        insert into announcements(
                            idempotency_key, source_id, source_channel_id, source_message_id,
                            source_message_ids, price, price_period, price_basis,
                            price_normalized_monthly, audience_tags, audience_excluded_tags,
                            occurred_at, is_promoted
                        )
                        values (
                            'telegram:-100:1:created', 'src-a', '-100', '1', array['1'],
                            500, 'monthly', 'total', 500, array['family'], array[]::text[],
                            now(), false
                        )
                        returning is_promoted
                        """
                    )
                )
                inserted = connection.execute(
                    text("select is_promoted from announcements limit 1")
                ).scalar_one()
                assert inserted is False

            assert "head" in current_run.stdout or "head" in current_run.stderr

            legacy_schema = f"legacy_bridge_{uuid4().hex[:8]}"
            _apply_legacy_sql_schema(settings, schema=legacy_schema)
            legacy_engine = create_engine(settings.sync_database_url)
            try:
                drift = compare_schema(legacy_engine, schema=legacy_schema)
                assert drift
                assert any(
                    item.table == "announcements"
                    and any("is_promoted" in issue for issue in item.column_issues)
                    for item in drift
                )

                with legacy_engine.begin() as connection:
                    connection.execute(text(f"set search_path to {legacy_schema}, public"))
                    connection.execute(text("update announcements set is_promoted = false"))
                    connection.execute(
                        text("alter table announcements alter column is_promoted set default false")
                    )
                    connection.execute(
                        text("alter table announcements alter column is_promoted set not null")
                    )

                fixed_drift = compare_schema(legacy_engine, schema=legacy_schema)
                assert not any(
                    item.table == "announcements"
                    and any("is_promoted" in issue for issue in item.column_issues)
                    for item in fixed_drift
                )
                # Legacy SQL is archive/reference only. Newer Alembic revisions add
                # schema that the legacy chain does not contain, so the bridge must
                # remain unsafe to stamp until explicit forward migrations close all
                # remaining drift.
                assert fixed_drift
                assert any(item.table == "ingestion_sources" for item in fixed_drift)
                assert "alembic_version" not in inspect(legacy_engine).get_table_names(
                    schema=legacy_schema
                )
            finally:
                legacy_engine.dispose()

        finally:
            engine.dispose()

    finally:
        if host_port is not None:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                check=False,
                capture_output=True,
                text=True,
            )


def _resolve_container_port(container_name: str) -> int:
    deadline = time.time() + 30
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "port", container_name, "5432"],
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        if output:
            port = output.rsplit(":", maxsplit=1)[-1]
            return int(port)
        time.sleep(0.5)
    raise TimeoutError("docker port never resolved")


def _require_docker() -> None:
    try:
        subprocess.run(
            ["docker", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Docker daemon unavailable for throwaway PostgreSQL integration smoke: {exc}")


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
        except Exception as exc:  # pragma: no cover - network timing loop
            last_error = exc
            time.sleep(0.5)
    raise TimeoutError("postgres never became ready") from last_error


def _alembic_env(settings: Settings) -> dict[str, str]:
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


def _apply_legacy_sql_schema(settings: Settings, *, schema: str) -> None:
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value() if settings.db_password else None,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("create extension if not exists pgcrypto")
            cur.execute(f"create schema {schema}")
            cur.execute(f"set search_path to {schema}, public")
            for migration in LEGACY_MIGRATIONS:
                cur.execute(migration.read_text(encoding="utf-8"))
    finally:
        conn.close()
