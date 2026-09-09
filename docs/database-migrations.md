# Database and migrations

PostgreSQL is the system of record. Runtime SQLAlchemy sessions use `postgresql+asyncpg`; Alembic uses `postgresql+psycopg2`. Deduplication also has asyncpg repositories. Models under `src/estateflow/models/` describe the current schema contract; `alembic/versions/` supplies executable schema evolution.

## Domain and integrity

- Sources, listener accounts and assignments keep transport configuration separate from parser policy.
- Announcements retain canonical/child links, source identity, listing attributes, confidence and provenance. A unique idempotency key and a partial unique source-message index prevent identity collisions among non-deleted rows.
- Media rows retain per-announcement storage identity. Check constraints bound statuses, price semantics, confidence and nonnegative physical values.
- Partial indexes cover active canonical search and dedup candidate lookups. GIN indexes support audience tag arrays.
- Saved filters, notifications and delivery attempts retain matching and delivery state. Review items, dedup decisions and merge audits preserve reasons for uncertain/merged listings.
- Feature-flag updates check `expected_version` under repository locking/transaction logic and increment the persisted version. This concurrency contract does not apply to all entities.

## Current chain

```text
5fdae000458c -> 9b77c0b3d5a1 -> c4d8e7f1a2b3
             -> e7f2a1b3c4d5 -> f8a3b2c4d5e6 -> a1c4e7f9b2d6
```

The latest revision adds source parser key/version/config/mode, validates JSON object/mode constraints, backfills legacy profiles, and permits `source_parser_decision` technical metrics. Its downgrade removes parser configuration and deletes parser metric rows: it is not a lossless rollback.

## Fresh databases

Configure an isolated target and run:

```powershell
.venv\Scripts\python.exe -m alembic heads
.venv\Scripts\python.exe -m alembic history
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current
```

`heads` and `history` inspect the graph without applying it. Apply migrations once before starting runtime processes. Production Compose supplies distinct `MIGRATION_DB_USER` / `MIGRATION_DB_PASSWORD`; runtime uses `DB_USER` / `DB_PASSWORD`. Role creation and grants remain operator responsibilities.

## Legacy database bridge

`migrations/*.sql` is retained for legacy schema inspection, bridge tests and reference. Do not apply the old SQL chain and Alembic baseline together on a fresh database. Use `estateflow-migrations bridge-check` to compare an existing legacy schema; resolve mismatches with reviewed forward changes before considering `bridge-stamp --confirmed`. Stamping records a revision without executing its migrations and must never substitute for schema verification. See `estateflow-migrations --help` for explicit DSN/schema options.

## Verification and recovery

`tests/test_sprint8_alembic_integration.py` creates a disposable PostgreSQL container and tests upgrade, parser downgrade/re-upgrade, constraints, smoke execution and legacy bridging. `scripts/migration_smoke.py` creates a test schema and checks the Alembic-managed contract; use only a disposable development target. Graph validation alone does not verify SQL execution.

Before real upgrades, retain a backup and verify restoration on a separate database. Prefer forward corrective migrations; do not drop application schemas, volumes, or data to repair revision drift. Database writes and queue publication are separate transactions, so recovery must also account for pending/replayed events.
