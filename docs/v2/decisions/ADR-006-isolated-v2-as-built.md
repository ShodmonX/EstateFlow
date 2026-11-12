# ADR-006 — As-built isolated V2 lab

Status: LAB_PROVISIONAL, 2026-11-10. User authorized M00–M10 execution without intermediate lab gates. Production policy is not approved. See verification/FINAL_V2_REPORT.md and V2_OPEN_ITEMS.md for actual limits.

Twelve independently running domain workers use schemas listener, source_parser, pre_ai, ai, catalog, search, notifications, identity, control, taxonomy, content and analytics. AI/media share ai; matcher/sender share notifications. Public/admin/internal/bot adapters have no database DSN. One image contains the monorepo; each process imports only its own handler plus technical shared/foundation modules. Independent process restart is supported; separate per-owner build/release pipelines are not delivered.

Each owner has records, schema_versions, outbox, inbox, revisions, retry_schedule, parked_events and replay_audit. Two additional synthetic foundation owners use identical technical tables. These are new JSON-document records, not migrated physical V1 tables; legacy ownership remains the 26-table map. Domain invariants are primarily application-enforced, not new relational FKs.

Bootstrap creates NOLOGIN owner and distinct NOINHERIT app/migration identities, safe schema-qualified access/default grants; runtime cannot SET ROLE owner. Shared lab bootstrap is privileged; per-owner upgrade CLI and database-enforced append-only audits remain open. Additive schema version2 separates retry transport attempts from handler processing attempts.

The isolated broker uses TOPIC estateflow.v2.events and DIRECT estateflow.v2.dlx. One consumer-owned v2.<owner>.events.v1 queue with exact versioned bindings per context is the as-built topology (shared/routing.py), with a durable .dead companion. Poison/conflicts are normally committed to owner parked_events and ACKed; .dead is the transport NACK(false) lane. These are distinct mechanisms. Classic queues/single node are lab-only. A broker confirm is not consumer processing.

Own domain+outbox and effect+inbox are transactional. Dispatch uses leases/fencing/mandatory confirms; consumer ACK follows durable effect/retry/park. Separate caps apply to handler, outbox and retry-transport work. Synthetic aggregate gaps are strict; business lifecycle uses owner-local source/generation/media/canonical watermarks. Authorized replay resets terminal retry budget with stable event identity and audit, without inbox deletion. Search rebuild serializes with live events and atomically switches a separately built projection namespace.

Listener/parser/AI/storage/senders use synthetic or offline providers. Media codec is real, data is synthetic. No Redis dependency or live session/provider implementation. Fake notification side effect occurs in one transaction and cannot prove a real external send protocol. Shared broker principal and self-asserted service delegation remain a security blocker, despite HTTP HMAC and SQL isolation.

Reviewed empty generations retain old evidence pending explicit review_batch approval. Corrected generations that supersede pending review remain reviewable under their new generation. Source-approval failed dependencies do not activate ingestion; already-granted rewards require manual reconciliation. These safe lab choices remain product decisions.

The final offline result may remain PARTIAL even with passing implemented tests: full legacy API/UI contracts, active-data import, production authorization, provider integration, retention, HA and load are separate gates. No change here authorizes V1 deployment, live credentials, real messages or production migrations.
