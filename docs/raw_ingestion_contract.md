# Raw Ingestion Queue Contract

## Queue

- Primary queue: `ingestion.raw_announcements`
- Dead-letter ready queue name: `ingestion.raw_announcements.dlq`
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
