# Sprint 2 AI Worker Handoff

## Input Queue

Sprint 2 AI workers consume:

```text
ai.processing.raw_announcements
```

This queue receives only raw ingestion events that did not receive a
`high_confidence_duplicate` Pre-AI decision.

## Raw Event Schema

Payload schema version: `telegram.raw.v1`

The raw event fields are documented in
[`raw_ingestion_contract.md`](raw_ingestion_contract.md). AI workers should treat
these fields as the source of truth and must not depend on Telethon objects.

Required raw fields:

- `schema_version`
- `event_type`
- `idempotency_key`
- `account_key`
- `source_id`
- `source_channel_id`
- `source_message_id`
- `source_identifier`
- `source_message_ids`
- `occurred_at`
- `correlation_id`
- `media`

Optional raw fields:

- `text`
- `forward_metadata`
- `media_group_id`

## Media References

`media` contains references and metadata only:

- `media_id`
- `media_type`
- `mime_type`
- `size_bytes`
- `access_hash`

No binary media is stored in Redis. Sprint 2 may download media in a worker stage,
then upload processed files to object storage.

## Pre-AI Decision

Each AI queue payload includes `pre_ai_dedup`:

- `decision`: `proceed_to_ai` or `needs_reviewable_signal`
- `should_skip_ai`: always `false` on this queue
- `reasons`
- `signals`
- `audit_reference`

Events with `high_confidence_duplicate` are not published to this queue. They
remain auditable through the Pre-AI decision audit sink.

## Idempotency And Retry

AI workers should use `idempotency_key` as the processing idempotency key.

If AI processing fails after dequeue:

- Retry with the worker retry policy.
- Preserve `correlation_id` in logs and Ops alerts.
- Send exhausted failures to `ingestion.raw_announcements.dlq` or a later
  AI-specific DLQ when introduced.

Raw text, phone numbers, account sessions, API hashes, and bot tokens must not be
logged in worker errors or Ops alerts.
