from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any
from uuid import uuid4

import psycopg2  # type: ignore[import-untyped]
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine

from estateflow.application.core.config import Settings, get_settings
from estateflow.db.migration_tools import current, normalize_schema_name, stamp_head, upgrade_head
from estateflow.db.schema_contract import EXPECTED_AUDIENCE_TAG_SEEDS
from estateflow.db.schema_inspection import compare_schema, format_drift, inspect_schema


class MigrationDatabaseError(RuntimeError):
    """A database connection failed without exposing its credential-bearing DSN."""


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="estateflow-migrations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade_parser = subparsers.add_parser("upgrade-head", help="Run alembic upgrade head.")
    _add_schema_argument(upgrade_parser)

    current_parser = subparsers.add_parser("current", help="Show current alembic revision.")
    _add_schema_argument(current_parser)

    bridge_parser = subparsers.add_parser(
        "bridge-check",
        help="Inspect a legacy database and report drift against ORM metadata.",
    )
    _add_bridge_arguments(bridge_parser)

    stamp_parser = subparsers.add_parser(
        "bridge-stamp",
        help="Stamp head only after an explicit successful bridge inspection.",
    )
    _add_bridge_arguments(stamp_parser)
    stamp_parser.add_argument(
        "--confirmed",
        action="store_true",
        help="Require an explicit confirmation to stamp head after inspection.",
    )

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Create a throwaway schema, upgrade head, and verify the migration contract.",
    )
    smoke_parser.add_argument(
        "--dsn",
        default=None,
        help="Optional PostgreSQL DSN. Defaults to typed Settings.",
    )
    smoke_parser.add_argument("--schema", default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = _settings_from_args(args)

    if args.command == "upgrade-head":
        upgrade_head(settings, schema=getattr(args, "schema", None))
        return
    if args.command == "current":
        current(settings, schema=getattr(args, "schema", None))
        return
    if args.command == "bridge-check":
        _bridge_check(settings, schema=args.schema, output_json=args.output_json)
        return
    if args.command == "bridge-stamp":
        _bridge_stamp(settings, schema=args.schema, confirmed=args.confirmed)
        return
    if args.command == "smoke":
        result = run_smoke(settings, dsn=args.dsn, schema=args.schema)
        print(json.dumps(result, sort_keys=True, default=str))
        return
    raise SystemExit(f"Unknown command: {args.command}")


def run_smoke(
    settings: Settings | None = None,
    *,
    dsn: str | None = None,
    schema: str | None = None,
) -> dict[str, object]:
    resolved = settings or get_settings()
    requested_schema = normalize_schema_name(schema)
    target_schema = requested_schema or f"estateflow_smoke_{uuid4().hex[:12]}"
    smoke_dsn = dsn or resolved.sync_database_url
    _ensure_database(smoke_dsn)
    _ensure_schema(smoke_dsn, target_schema)
    try:
        upgrade_head(resolved, schema=target_schema)
        result = _legacy_smoke_contract(smoke_dsn, schema=target_schema)
        result["revision"] = _current_revision(smoke_dsn, schema=target_schema)
        return result
    finally:
        if requested_schema is None:
            _drop_generated_smoke_schema(smoke_dsn, target_schema)


def _bridge_check(
    settings: Settings | None = None,
    *,
    schema: str | None = None,
    output_json: bool = False,
) -> None:
    resolved = settings or get_settings()
    engine = _engine_for_settings(resolved)
    drift = compare_schema(engine, schema=schema)
    fingerprint = inspect_schema(engine, schema=schema)
    if output_json:
        print(
            json.dumps(
                {
                    "drift": [asdict(item) for item in drift],
                    "tables": {
                        name: {
                            "columns": [asdict(column) for column in table.columns],
                            "primary_key": list(table.primary_key),
                            "unique_constraints": [list(item) for item in table.unique_constraints],
                            "foreign_keys": [
                                {
                                    "name": fk[0],
                                    "referred_table": fk[1],
                                    "constrained_columns": list(fk[2]),
                                    "referred_columns": list(fk[3]),
                                }
                                for fk in table.foreign_keys
                            ],
                            "indexes": [asdict(index) for index in table.indexes],
                            "checks": list(table.checks),
                        }
                        for name, table in fingerprint.tables.items()
                    },
                    "audience_tags": fingerprint.audience_tags,
                    "index_names": list(fingerprint.index_names),
                },
                sort_keys=True,
                default=str,
            )
        )
    else:
        if drift:
            raise SystemExit(format_drift(drift))
        print("schema matches ORM metadata and seed contract; safe to stamp after review.")


def _bridge_stamp(
    settings: Settings | None = None,
    *,
    schema: str | None = None,
    confirmed: bool,
) -> None:
    resolved = settings or get_settings()
    engine = _engine_for_settings(resolved)
    drift = compare_schema(engine, schema=schema)
    if drift:
        raise SystemExit(format_drift(drift))
    if not confirmed:
        raise SystemExit("--confirmed is required to stamp head after bridge inspection.")
    stamp_head(resolved, schema=schema)
    print("stamped head after successful inspection")


def _legacy_smoke_contract(dsn: str, *, schema: str | None) -> dict[str, object]:
    normalized_schema = normalize_schema_name(schema)
    resolved_schema = normalized_schema or "public"
    conn = _connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            if normalized_schema is not None:
                cur.execute(f"create schema if not exists {normalized_schema}")
                cur.execute(f"set search_path to {normalized_schema}, public")
            else:
                cur.execute("set search_path to public")
            cur.execute("create extension if not exists pgcrypto")

            cur.execute("insert into listener_accounts(account_key) values ('listener-a')")
            cur.execute(
                """
                insert into ingestion_sources(
                    source_id, name, source_type, identifier, listener_account_key
                )
                values
                    ('src-a', 'A', 'telegram_channel', '@a', 'listener-a'),
                    ('src-b', 'B', 'telegram_channel', '@b', 'listener-a')
                """
            )
            cur.execute(
                "insert into users(user_id, referral_code) values (1001, 'ref1001'),"
                " (1002, 'ref1002')"
            )
            cur.execute(
                """
                insert into referral_events(
                    referrer_user_id, referred_user_id, activation_event_key, status
                )
                values (1001, 1002, 'search-created:1002', 'active')
                """
            )
            cur.execute(
                """
                insert into source_suggestions(user_id, source_identifier)
                values (1001, '@candidate-channel')
                """
            )
            cur.execute(
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
                returning announcement_id
                """
            )
            parent_id = cur.fetchone()[0]
            cur.execute(
                """
                insert into announcements(
                    idempotency_key, source_id, source_channel_id, source_message_id,
                    source_message_ids, parent_announcement_id, price, price_period,
                    price_basis, price_normalized_monthly, audience_tags,
                    audience_excluded_tags, occurred_at
                )
                values (
                    'telegram:-200:7:created', 'src-b', '-200', '7', array['7'],
                    %s, 500, 'monthly', 'total', 500, array['family'],
                    array[]::text[], now()
                )
                returning announcement_id
                """,
                (parent_id,),
            )
            child_id = cur.fetchone()[0]
            cur.execute(
                """
                insert into announcement_media(
                    media_id, announcement_id, storage_url, object_key, mime_type,
                    size_bytes, phash, content_sha256
                )
                values (
                    'm1', %s, 'memory://m1', 'obj/m1.jpg', 'image/jpeg', 10,
                    '0000000000000000', 'sha'
                )
                """,
                (parent_id,),
            )
            cur.execute(
                """
                insert into analytics_events(
                    event_name, idempotency_key, occurred_at, user_id, subject_id, metadata
                )
                values (
                    'search_completed', 'analytics:search:1001', now(), 1001, 'search-1001',
                    '{"source":"smoke"}'::jsonb
                )
                """
            )
            cur.execute(
                """
                insert into technical_metric_events(
                    metric_name, idempotency_key, occurred_at, value,
                    component, subject_id, metadata
                )
                values (
                    'queue_delay_ms', 'technical:queue:1001', now(), 12.5, 'worker-a',
                    'queue-1001', '{"source":"smoke"}'::jsonb
                )
                """
            )
            cur.execute(
                """
                insert into dedup_decisions(
                    announcement_id, candidate_announcement_id, decision, score,
                    threshold, config_version, score_breakdown
                )
                values (
                    %s, %s, 'high_confidence_duplicate', 85, 80,
                    'estateflow.dedup.v1', '[]'::jsonb
                )
                """,
                (child_id, parent_id),
            )
            cur.execute(
                """
                insert into announcement_merge_audit(
                    parent_announcement_id, child_announcement_id, merge_policy, conflicts
                )
                values (%s, %s, 'smoke', '[]'::jsonb)
                """,
                (parent_id, child_id),
            )
            cur.execute(
                """
                select count(*)
                from announcements
                where parent_announcement_id is null and status = 'active'
                """
            )
            parent_count = cur.fetchone()[0]
            cur.execute(
                """
                select is_nullable, column_default
                from information_schema.columns
                where table_schema = %s
                  and table_name = 'announcements'
                  and column_name = 'is_promoted'
                """,
                (resolved_schema,),
            )
            promoted_nullable, promoted_default = cur.fetchone()
            cur.execute(
                """
                select count(*)
                from audience_tag_types
                where status = 'approved'
                """
            )
            seed_count = cur.fetchone()[0]
        return {
            "schema": normalized_schema,
            "parent_count": parent_count,
            "is_promoted_nullable": promoted_nullable,
            "is_promoted_default": promoted_default,
            "approved_seed_count": seed_count,
            "seed_tags_expected": len(EXPECTED_AUDIENCE_TAG_SEEDS),
        }
    finally:
        conn.close()


def _current_revision(dsn: str, *, schema: str | None) -> str | None:
    normalized_schema = normalize_schema_name(schema)
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            if normalized_schema is not None:
                cur.execute(f"set search_path to {normalized_schema}, public")
            cur.execute("select version_num from alembic_version")
            row = cur.fetchone()
            return None if row is None else str(row[0])
    finally:
        conn.close()


def _ensure_database(dsn: str) -> None:
    conn = _connect(dsn)
    conn.close()


def _ensure_schema(dsn: str, schema: str) -> None:
    conn = _connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"create schema if not exists {schema}")
    finally:
        conn.close()


def _drop_generated_smoke_schema(dsn: str, schema: str) -> None:
    if not schema.startswith("estateflow_smoke_"):
        raise ValueError("Refusing to drop a schema not generated by migration smoke.")
    conn = _connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{schema}" cascade')
    finally:
        conn.close()


def _engine_for_settings(settings: Settings) -> Engine:
    return create_engine(settings.sync_database_url)


def _connect(dsn: str) -> Any:
    try:
        return psycopg2.connect(_normalize_psycopg_dsn(dsn))
    except Exception:
        raise MigrationDatabaseError(
            "PostgreSQL connection failed; verify the configured host, port, database, and role."
        ) from None


def _normalize_psycopg_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+") and "://" in dsn:
        return f"postgresql{dsn[dsn.index('://') :]}"
    return dsn


def _add_schema_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--schema", default=None)


def _add_bridge_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dsn",
        default=None,
        help="Optional PostgreSQL DSN. Defaults to typed Settings.",
    )
    parser.add_argument("--schema", default=None)
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print a JSON fingerprint instead of a human-readable verdict.",
    )


def _settings_from_args(args: argparse.Namespace) -> Settings | None:
    if getattr(args, "dsn", None):
        return _settings_from_dsn(args.dsn)
    return get_settings()


def _settings_from_dsn(dsn: str) -> Settings:
    from urllib.parse import urlparse

    parsed = urlparse(dsn)
    db_password = parsed.password or None
    return Settings(
        environment="test",
        db_driver="postgresql+asyncpg",
        db_sync_driver="postgresql+psycopg2",
        db_host=parsed.hostname or "localhost",
        db_port=parsed.port or 5432,
        db_name=(parsed.path or "/estateflow").lstrip("/"),
        db_user=parsed.username or "estateflow",
        db_password=None if db_password is None else SecretStr(db_password),
    )
