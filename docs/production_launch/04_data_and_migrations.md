# Data and Migrations Plan

## Objective

Production database ni xavfsiz yangilash va rollback qilish mumkin bo'lishini ta'minlash.

## Tasks

- Production backup olish.
- Backup checksum yoki verify step yozish.
- Restore procedure ni throwaway environmentda sinash.
- Migrationlar ketma-ketligini tekshirish.
- Forward compatibility va rollback strategy yozish.
- Throwaway schema yoki staging DB da Alembic `base` dan `head` gacha migration smoke qilish.
- Data-loss xavfi bo'lgan migratsiyalar uchun owner signoff olish.

## Exit criteria

- Restore test successful.
- Migration apply clean.
- Rollback path documented and tested.

## EstateFlow migration path

### Fresh database

Use Alembic against an empty PostgreSQL database. The supported entrypoints are the
direct Alembic CLI or the repo command wrapper:

```powershell
.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.venv\Scripts\python.exe -m estateflow.db.migration_cli upgrade-head
```

This applies the supported Alembic revision chain in order:

- `5fdae000458c` — full production schema baseline.
- `9b77c0b3d5a1` — ingestion source profile.
- `c4d8e7f1a2b3` — media-per-announcement key.
- `e7f2a1b3c4d5` — manual-review queue backfill.
- `f8a3b2c4d5e6` — approved manual-review activation (`head`).

The `migrations/001...011_*.sql` files are legacy archive/reference material. They
must not be executed as the fresh-production migration path.

### Existing legacy database

Do not stamp `head` until schema inspection is complete.

Recommended bridge flow:

1. Run the schema inspection helper against the target database.
2. Compare table and column names to the ORM metadata.
3. If the schema matches, stamp the database to `head`.
4. If drift exists, resolve it with a documented migration or a controlled data fix.
5. Never auto-stamp from application startup or deployment bootstrap.

Inspection helper:

```powershell
.venv\Scripts\python.exe scripts\schema_bridge_check.py --dsn postgresql+psycopg2://...
.venv\Scripts\python.exe -m estateflow.db.migration_cli bridge-check --dsn postgresql+psycopg2://...
```

If the output reports a clean match, the operator can proceed with:

```powershell
.venv\Scripts\python.exe -m alembic -c alembic.ini stamp head
.venv\Scripts\python.exe -m estateflow.db.migration_cli bridge-stamp --dsn postgresql+psycopg2://... --confirmed
```

### Local smoke validation

The smoke command now uses Alembic against a throwaway schema and inserts sample rows
with a non-null `is_promoted` value so the old false-positive `null` path is gone:

```powershell
.venv\Scripts\python.exe scripts\migration_smoke.py --dsn postgresql://...
.venv\Scripts\python.exe -m estateflow.db.migration_cli smoke --dsn postgresql://...
```

The old `scripts/migration_smoke.psql` file is deprecated and intentionally fails if
invoked. Use Alembic-based commands instead.

### Docker compose release step

The API container must not run migrations in-band. Use the one-shot profile service:

```powershell
docker compose --profile migrations run --rm migrate
```

### Runtime process split

Start the application processes separately:

```powershell
estateflow-api
estateflow-bot
estateflow-worker
```

All Compose environments connect to the password-protected DigitalOcean
PostgreSQL service. No PostgreSQL container or trust authentication is used.

### Release controls

Feature flags are stored in PostgreSQL and audited immutably. The runtime does not
use process-local memory as the source of truth, so a restart or a second process
sees the same flag state.

Use the admin API with an explicit version check when changing a flag:

```powershell
GET /admin/release/flags
POST /admin/release/flags
```

The change body must include `flag_name`, `enabled`, `expected_version`, and
`reason`. The server derives the audit actor from deployment config and records the
previous/new value, version, actor, reason, and timestamp.

Rollback steps for a bad flag change:

1. Read the current version from `GET /admin/release/flags`.
2. Disable the flag with the current `expected_version`.
3. Verify the new version and audit row were written.
4. If the feature is queue-backed, pause the corresponding worker or publisher
   before re-enabling anything else.
5. There is no cache propagation wait in the current design because the runtime
   reads PostgreSQL directly.
6. If the wrong database row was changed, do not rewrite history in place; apply a
   forward fix or restore from backup.
