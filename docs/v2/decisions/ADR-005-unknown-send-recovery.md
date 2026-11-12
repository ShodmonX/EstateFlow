# ADR-005 — Notification and content unknown-send recovery

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See ../verification/FINAL_V2_REPORT.md and ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **OPEN / REQUIRES USER APPROVAL (D08)**. Date: 2026-11-09.

VERIFIED: active SQLAlchemy sender claims with an unlocked SELECT then commits sending (`src/estateflow/repositories/sqlalchemy.py:1205–1220`), Telegram send precedes sent persistence (`src/estateflow/services/notifications.py:449–494`), and split worker has no due scanner (`services/worker_notification_delivery/main.py:43–51`). Content publish also precedes persisted result (`src/estateflow/services/content_automation.py:298–315`). Outbox/inbox cannot atomically commit a Telegram side effect.

Confirmed design constraint: notifications owns filters/jobs/attempts initially; matcher/sender can be separate processes. No exactly-once Telegram notification promise.

Propose atomic owner-local lease claims with fencing/expiry and durable attempt identity. Failure definitely before external acceptance can schedule a bounded retry. Timeout, process crash after submission or missing persisted response becomes unknown, not automatically failed/sent. Expired lease does not prove that the external send did not happen. Proposed default: hold unknown for audited operator resolution (suppress, confirmed sent, or explicitly authorize resend), using original job and a new attempt/command ID; retain actor/reason. Generic recovery/projection replay must not trigger sends. Consent/entitlement checks and immutable intended content hash recorded for each attempt; policy for stale identity projections remains D08.

Alternative: bounded automatic resend improves eventual delivery but may duplicate notifications/posts. Automatic suppression reduces duplicate risk but can lose an unsent message. No adapter or event can conjure an external idempotency guarantee absent provider support. Approval must choose unknown handling, authority, retry age/caps/rate limits and per-update alert policy.

Required proof before sender cutover: competing claims, due time across restart, 429/blocked user/permanent error, crash immediately before/after send and after sent commit before ACK, consent revocation, same-body replay and isolated rebuild. Fake sender tests must assert explicit unknown outcome, not merely count sends in a successful happy path. Real send/production operations require later authorization.
