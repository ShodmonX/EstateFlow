# Operations and implementation limits

## Observe the pipeline

Use correlation IDs to follow listings through raw, AI, dedup and notification logs. API `/health/live` is process liveness; `/health/ready` checks PostgreSQL, Redis and the queue backend. Standalone worker health checks only test whether PID 1 exists; they do not prove queue progress.

Monitor ready/unacknowledged messages, each stage's `.dlq`, parser quarantine, parser decisions by source/version, AI fallback/validation metrics, review backlog, and notification attempts. `announcement.persisted` accumulates while the optional matcher is absent. Decide retention/capacity before long ingestion-only runs.

```bash
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml ps -a
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

Keep raw listing text, phone numbers, sessions and credentials out of public logs and incident artifacts. Redaction utilities and tests exist, but review captured output before sharing it.

## Retry and replay

The RabbitMQ transport republishes failed work immediately with an incremented retry count, then acknowledges the original. Exhausted deliveries are rejected to `estateflow.events.dlx` and `<queue>.dlq`; malformed JSON is also rejected without requeue. There is no broker delay/backoff topology. Broker durability is not an atomic transaction with PostgreSQL.

Inspect failed payloads privately, correct the cause, and replay deliberately with original correlation/provenance and idempotency fields intact. There is no automated quarantine replay service. Preserve quarantined segments while adjusting source recipes; start policy changes in shadow mode and evaluate labeled posts before activation. A custom parser `buffer` result currently fails open because persistent cross-message buffering is unavailable.

Redis listener idempotency and database uniqueness help with replays. Pre-AI duplicate indexes, AI processing records and some audits are in-memory in split worker construction and reset on restart. A successful DB write followed by publish failure, or a successful Telegram send followed by state-write failure, remains a reconciliation risk. There is no transactional outbox.

Notification delivery records include `next_attempt_at`, rate-limit handling and attempt history. The split delivery handler calls `process_job`; its queue runner acknowledges a normally returned handler even if a retry was recorded in the database. A delayed retry dispatcher is not wired into that split entrypoint. Inspect pending due jobs explicitly; do not assume broker retries schedule all notification retries.

## Media limitation

`MediaProcessor._compress_image` computes target dimensions but retains input bytes when below the limit and truncates bytes plus a digest when above it. It does not decode/resize/re-encode images; oversized output may be invalid, and reported dimensions may differ from the encoded image. The `phash` implementation is a byte-derived similarity signal, not verified perceptual hashing. Current tests do not establish image-decoder validity. Replace this routine with a real image encoder and decoder-backed regression tests before depending on transformed production media. Object-storage uploads and format/header checks are separate implemented capabilities.

## Administrative contracts

Keep admin and internal APIs private. Admin requests use `X-Admin-Token`; WebApp authentication verifies Telegram `initData` rather than trusting a client-supplied user ID. Feature-flag changes require the expected version and retain audit records. Review source suggestions, pending audience tags and manual-review items; disabling an incorrectly approved source preserves historical announcements.

Audience tags are flat. Tag merging remaps announcement/filter references and preserves audit history. Content publishing stores attempt/due state and can run as a dry-run when credentials are absent; review release flags and daily limits before enabling external sends.

## Configuration and backup hygiene

Store `.env*`, Telegram sessions, object-storage keys, local data and dumps outside publication candidates. `.gitignore` does not remove previously committed secrets. Before public release, inspect reachable history as well as the working tree. Rotate an externally exposed credential at its provider; replacing an example does not invalidate it.

Use PostgreSQL client password files or a secret manager rather than embedding passwords in command-line DSNs. Back up PostgreSQL and authorized sessions privately, keep an off-host copy, and rehearse restoration. Queue/cache volumes are separate from database persistence. Record schema revision, configuration versions and image identity with each release. Never use volume deletion or an unreviewed schema stamp as recovery.
