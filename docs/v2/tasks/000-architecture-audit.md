# Task 000 — Read-only V1 Architecture Audit

Status: READY FOR CODEX

## Goal
Build an evidence-based map of the existing EstateFlow codebase before any V2 implementation.

## Allowed
Read source/config/docs, inspect git history, run safe read-only checks and existing tests in disposable environments, write ONLY V2 audit documents and current-state handoff.

## Forbidden
Changing application code, migrations, deployment manifests, secrets, production data, Git history; commits/pushes; starting listeners against real Telegram sessions; connecting to live systems without permission.

## Deliverables
- `docs/v2/architecture-audit.md`
- `docs/v2/dependency-matrix.md`
- Proposed table ownership mapping and event catalog (append to audit or separate files)
- Update `docs/v2/current-state.md`

## Required coverage
1. All service entrypoints and Docker build contexts.
2. Import graph and shared domain/repository usage.
3. All PostgreSQL table readers/writers, schema migrations, cross-domain transactions.
4. RabbitMQ exchanges, queues, consumers, retry/ack/DLQ/replay semantics.
5. Redis and in-memory state that affects correctness after restart.
6. Listener/source-parser/pre-AI behavior including edits, albums, quarantines.
7. AI/media/post-AI/search/notification reliability and boundaries.
8. Security, CI, testing, production cutover risks.

## Acceptance criteria
- Every nontrivial finding has path + line evidence.
- Facts separated from hypotheses.
- Missing coverage explicitly listed.
- No app code changed.
- User receives summary with top 10 coupling risks and 3 recommended next tasks.
