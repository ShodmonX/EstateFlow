# D01–D08: provisional LOCAL LAB choices to keep Codex moving

These defaults are **engineering assumptions within the user-approved isolated test stack**, not ratified production/business policy. Codex must not stop for permission; choose the safe assumption below, clearly label it `LAB_PROVISIONAL`, add an ADR/deviation if adjusted, and include each unresolved production question in `V2_OPEN_ITEMS.md`.

| ID | Local lab behavior | Production review still required |
|---|---|---|
| D01 | Use Task 001's exact 26-table/12-context map. `parsing_logs`/`processing_jobs` preserved in AI compatibility record or archived mapping, not dropped. AI/media initially one deployment; notification matcher/sender one data owner. | Real data inventory, grants, external writers, backfill and cutover map |
| D02 | Isolated topic `estateflow.v2.events`, own DLX, consumer-owned durable queues; owner-local PostgreSQL outbox/inbox; scheduled retry 5/15/45/135/405s ±20%, 6 total attempts; 60s lease, 10s confirm timeout, bounded prefetch, 24h synthetic event age; no auto purge. | Broker HA/queue type, load budgets, timeouts, replay authority and retention |
| D03 | Locally observed monotonic per-source revision, explicit uncertainty parking; tombstone blocks stale resurrection; other evidence keeps canonical visible; final active source removal hides listing; reparse creates explicit supersession and reviewable lineage. | Real Telegram reconciliation, restore rights, source correction and publication policy |
| D04 | Durable album/thread journal; 3s default settle, 60s max wait, 100 members and 1 MiB per synthetic group; overflow/unsupported recipe/quarantine closed/parked with reason; digest partial state explicit. | Real traffic thresholds, retention, operator workflow |
| D05 | Text-first permissible with `pending`/`failed` state; `media_required` blocks publication pending review; 30s synthetic media deadline; fake storage by default; media ready creates later catalog revision. | Media entitlement, user notification updates, object TTL/capabilities |
| D06 | Async accepted command with ID; bounded 15s operation status polling for tests; owner-authorized commands, no cross-schema adapter SQL; failures explicit. | Interactive UX/error mapping/timeouts, real auth delegation |
| D07 | Synthetic values only; no automatic deletion for lab state; redact logs, cap payload sizes, audited replay; no real PII, contact/address samples identifiable to persons or credentials. | ACL/PII classification, explicit retention/erasure windows, authorized replay/legal needs |
| D08 | Fake sender only; unknown send result = `UNKNOWN` + manual reconciliation queue, never automatic resend; initial listing alert once per user/filter/canonical ID; edited alerts off; consent/entitlement checked at send time; stale projection parks. | Real sender duplicate/loss tradeoff, send rates, consent freshness, edit alerts |

## Emergency assumptions

- Security/privacy ambiguity → deny, park, audit.
- Loss vs duplication risk → preserve data, allow at-least-once broker retry, deduplicate local effects.
- User-visible external effect ambiguous → **do not resend automatically**.
- Unknown source ordering → park/reconcile, do not discard or resurrect stale canonical data.
- External credentials unavailable → run feature on explicit offline fake adapter and report MOCK_ONLY, never silently skip feature.
- Unknown data migration → preserve raw/synthetic mapping and document uncertainty, never drop records.

No provisional choice permits connection to production, use of real credentials, public port binding or live outbound sends.
