# Raw Ingestion Queue Contract

## Queue

- Primary queue: `ingestion.raw_announcements`
- Dead-letter ready queue name: `ingestion.raw_announcements.dlq`
- Parser quarantine queue: `ingestion.source_parser.quarantine`
- Current payload schema version: `telegram.raw.v1`

## Idempotency

Single message key:

```text
telegram:{source_channel_id}:{source_message_id}:{event_type}
```

Album key:

```text
telegram:{source_channel_id}:album:{media_group_id}:{event_type}
```

The idempotency key deliberately excludes listener account key, so the same public
channel message observed by two listener accounts is still one logical raw event.
The key is marked as seen only after queue publish succeeds.

## Payload Fields

Required fields:

- `schema_version`
- `event_type`: `created`, `edited`, or `deleted`
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

Optional fields:

- `text`
- `forward_metadata`
- `media_group_id`
- `source_url`: public Telegram message URL when the source channel has a username
- `parser_key`: versioned recipe/plugin key selected for this source
- `parser_version`: immutable parser recipe version
- `parser_config`: validated source-specific recipe overrides (JSON object, at most 32 KiB)
- `parser_mode`: `active`, `shadow`, or `disabled`
- `parser_diagnostics`: deterministic parser decision details; empty on the transport event
- `parser_provenance`: raw-event/candidate identifiers populated after source parsing

Older `telegram.raw.v1` producers may omit all parser fields. Consumers must then
use the safe `generic.single_listing` recipe. The legacy `source_profile` value is
never required in a queue payload.

## Source parsing and candidate fan-out

The Pre-AI stage routes each immutable raw event by `source_id` and parser metadata.
A parser returns `drop`, `buffer`, `quarantine`, or `emit`; an `emit` result contains
one or more deterministic candidates. Each AI-bound candidate retains the raw
message references and adds a `source_parsing` object with:

- parser key, version, family, mode, decision, score, reason codes and signals;
- stable candidate ID, index and total candidate count;
- trusted extraction defaults/prompt hints;
- raw-event provenance, including original text and serializable media references;
- shadow-parser output when `parser_mode=shadow`.

For a one-candidate compatibility recipe, the original idempotency key is preserved.
Fan-out candidates use
`{raw_idempotency_key}:candidate:{stable_candidate_id}` so deduplication and retries
remain deterministic. Their persistence-facing source message identity uses the raw
Telegram message ID plus the candidate index; raw Telegram IDs remain in provenance.
`shadow` evaluates the configured parser but emits the legacy
single candidate; `disabled` skips parsing and also emits the compatibility candidate.
Active quarantine outcomes, including individual rejected digest segments, are copied
to the durable parser quarantine queue before the raw queue message is acknowledged.
Until a persistent stateful buffer is configured, a `buffer` decision is recorded for
audit and fails open to the compatibility candidate with
`buffer_unavailable_fail_open`; a raw event is never silently acknowledged and lost.

## Media References

Queue payloads store serializable media references only:

- `media_id`
- `media_type`
- `mime_type`
- `size_bytes`
- `access_hash`

Large binary media is never written to Redis. Media download/upload belongs to a
later worker stage. Temporary downloaded files must live outside the listener hot
path and be deleted after upload or after dead-letter/manual review handoff.

## Album Debounce

`TELEGRAM_ALBUM_DEBOUNCE_SECONDS` controls how long ingestion waits for additional
messages in the same Telegram media group. When the timer expires, the buffered
messages are merged into one canonical raw event. If publish fails, the album
buffer is retained and can be retried; the idempotency key is not marked seen.
