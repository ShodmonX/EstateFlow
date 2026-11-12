# V2 deviations and engineering rulings

2026-11-10: Entire isolated lab M00–M10 authorized by current user and master prompt. Older Task 001/002 STOP gates superseded for lab continuation only. D01–D08 defaults LAB_PROVISIONAL, no production acceptance.

Work in isolated new paths in current checkout; preserve untracked source specifications and V1. No new worktree/commit because user forbids commits and requires these on-disk checkpoints.

M00 unreleased Task001 reconciliation: envelope enum adds lab synthetic/command/domain_record kinds and lab owners; core fields/identity preserved. Added closed synthetic/command/domain-fact schemas instead of bypassing validation. Listing shared type adds optional V1 parity fields (no removal/change to original ten fields). Strict candidate version identity remains. Changes are LAB_PROVISIONAL, not released-schema mutation.

M02–M07 storage layout uses new owner-local JSON document records with PostgreSQL transactional inbox/outbox, not physical ALTER/movement of 26 legacy tables; synthetic compatibility importer will preserve owner/kind/legacy IDs. No real-data migration implied.

Synthetic credential handling incident: an initial broad image COPY included generated lab credentials (fixed via explicit COPY and Dockerfile.dockerignore; rotated before infrastructure startup). A later pytest long traceback rendered synthetic DB/broker environment values during a guard failure. Short tracebacks and sanitized capture required, plus final fresh-lab credential rotation. No real provider or production secret accessed; no global image/volume cleanup permitted. Old untagged image cache may retain INVALIDATED initial lab values, so operator must not reuse them.

Pre-AI reliability correction: exact-text signals suppress a second source only after a catalog snapshot confirms active evidence. Signals from failed/unconfirmed extraction do not suppress new evidence. Catalog snapshot/retraction bindings now include pre_ai for owner-local signal confirmation/invalidation; no cross-owner SQL introduced. Proven regression initially failed as duplicate instead of accepted. This safely changes the audited V1 pre-AI failure window rather than preserving destructive suppression.

As-built M01–M10 rulings: one queue per owner with exact versioned bindings (shared/routing.py), rather than one queue for every event type; owners share one lab image but run independently. No Redis dependency. New owner-local records preserve transactional inbox/outbox but do not implement the proposed relational physical migration. Per-owner migration identities exist; bootstrap still applies all owners via lab admin.

Review-driven fixes, each with observed failing regression then passing check: stale input generations, atomic approved digest replacement, empty-generation review, corrected pending-review generation, delayed media revisions, search rebuild/live-event serialization, delegated command failure results, missing action-specific fields, terminal retry reset, independent retry transport cap, unexpected application-exception retry budget and terminal content hold. New tests do not establish production provider behavior.

M09 evidence distinguishes actual new PostgreSQL/RabbitMQ containers and synthetic owner data from fake AI/storage/send effects. Backfill archives26 rows only; no active entity/FK migration is claimed. Restore rehearsal creates a new lab database in the same cluster and compares112 table content/count digests; no destructive rollback, PITR or real-data restore.

Final private credentials are newly generated in ignored .env.verification.local; old project stopped with volumes preserved. Short traceback policy used for fresh runs. Historical cache/old env files remain local and must not be reused; no automatic global cleanup. Runtime inspection excludes ephemeral test clients that deliberately mount ignored evidence and asserts all 20 declared infrastructure/runtime containers. First inspector attempt incorrectly included a test client and failed; corrected the selection, then inspected actual mounts/ports/network successfully.

Numeric deadline limitation: socket_timeout and blocked_connection_timeout are10 s in Broker; these do not independently prove a10 s publisher-confirm deadline under every half-open connection. FND06 injects an exception after actual confirmed acceptance. A proper confirm deadline/blackhole-network test is still required before production.
