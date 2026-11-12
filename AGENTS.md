# EstateFlow — Codex repository instructions

## Mission
Migrate EstateFlow V1 incrementally to V2: independently deployable services communicating through versioned RabbitMQ events; a shared PostgreSQL instance with strict service-owned schemas and roles. Preserve existing behavior and data unless a reviewed task explicitly changes it.

## Session startup (mandatory)
1. Read `docs/v2/current-state.md` and `docs/v2/architecture-v2.md`.
2. Read `docs/v2/implementation-plan.md` and the current task in `docs/v2/tasks/`.
3. Read only relevant V1 docs/source files; avoid scanning the entire repository by default.
4. Inspect `git status`, branch and recent commits. Never overwrite unrelated user changes.
5. State intended scope and verification before edits.

## Non-negotiable boundaries
- No direct import of another service's business logic, repositories or ORM models.
- No cross-service database reads/writes or cross-schema FKs. Runtime roles only access owned schemas.
- RabbitMQ events are versioned, schema-validated and backward compatible; queues are consumer-owned.
- Persistent idempotency for consumers; transactional outbox for DB-to-broker publishing.
- No credentials, Telegram sessions, raw private messages, or user PII in logs or commits.
- Never use destructive migration, `alembic stamp`, volume deletion, force-push or production operations without explicit authorization.
- Do not rewrite multiple services in one task. Preserve V1 while extracting V2 incrementally.
- Never assert that tests passed unless executed and outputs inspected.

## Workflow per task
1. Read task scope and acceptance criteria.
2. Produce a short dependency/impact plan, including events, schemas, consumers and rollback.
3. Implement smallest independently testable change.
4. Run focused tests, type/lint checks, contract tests, then integration tests as appropriate.
5. Record changed files, decisions, test evidence, risks, and next action in `docs/v2/current-state.md`.
6. Stop at phase gates; do not silently proceed to next phase.

## References
- `docs/v2/architecture-v2.md` — target boundaries and invariants
- `docs/v2/event-contracts.md` — messaging specification
- `docs/v2/database-ownership.md` — PostgreSQL isolation
- `docs/v2/implementation-plan.md` — migration gates
- `docs/v2/architecture-audit.md` — evidence to be completed
- `docs/v2/decisions/` — architecture decisions
- `docs/architecture.md`, `docs/operations.md`, `docs/database-migrations.md` — V1 ground truth

## Definition of done
Code + migrations + tests + operational notes + compatibility evidence + updated current state. No task is done with only green unit tests if it changes persistence or messaging.
