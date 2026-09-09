# Post-AI Deduplication Handoff Contract

## Queue

- Queue: `dedup.post_ai.structured_announcements`
- Payload schema version: `estateflow.announcement.v1`

Validated and normalized announcements are published here. Valid low-confidence
or missing-required-field results can also be forwarded with manual-review reasons
so the dedup stage persists their review state. Malformed output and failed model
or storage operations do not become successful canonical announcements.

## Idempotency

`idempotency_key` follows the raw ingestion contract, including stable candidate
suffixes when source parsing fans out. Consumers must
treat it as the unique processing key and make writes idempotent.

## Required Payload Fields

- `schema_version`
- `idempotency_key`
- `source_id`
- `source_channel_id`
- `source_message_id`
- `source_message_ids`
- `occurred_at`
- `correlation_id`
- `canonical`
- `media`
- `pre_ai_dedup`
- `dedup_signals`

## Canonical Listing Rules

`canonical` contains validated business fields only. It includes:

- `price_period`: `daily`, `monthly`, or `one_time`
- `price_basis`: `total` or `per_person`
- `price_normalized_monthly`: `price * 30` for daily prices, original price for
  monthly prices, and `null` for one-time sale prices
- flat `audience_tags` and `audience_excluded_tags`
- no structured amenities tags

If an announcement has at least one stored image, `renovation_level` may come
from Vision or from validated text extraction. The AI worker only routes the
record to manual review when confidence or other business checks fail, not
solely because Vision was unavailable.

## Media

`media` contains only successfully uploaded, non-near-duplicate images:

- `media_id`
- `storage_url`
- `object_key`
- `mime_type`
- `size_bytes`
- `phash`
- `content_sha256`
- `width`
- `height`

Raw image binaries and storage credentials are not payload fields. `storage_url`
is derived from the configured public base URL or endpoint; avoid embedding
credentials or signed URL tokens in that configuration.

## Dedup Signals

`dedup_signals.media_phashes` is available for image matching. Phone presence is
only a weak helper signal and must never decide duplicates on its own. The v2
policy requires an evidence-backed anchor for `possible_duplicate`; a phone
number plus the complete structured fingerprint (district, rooms, area,
normalized monthly price, and floor) is treated as a high-confidence duplicate.
Generic description/address overlap without that corroboration is not enough to
create a manual-review item.

`dedup_signals.media_phashes` uses the current byte-derived media hash. A hash-only
match is not a complete duplicate decision or verified perceptual image matching.
See [media limitations](operations.md#media-limitation).

## Processing Audit

AI processing repository records include:

- `idempotency_key`
- `status`
- `correlation_id`
- `canonical` when validation/normalization succeeded
- `media` after successful uploads
- model `attempts` with fallback stage, latency, status, token usage keys, and
  provider request id when available
- `error_type`
- `failure_reason`
- `post_ai_published`

The worker saves a canonical processing record, publishes to this queue, then
marks `post_ai_published=true`. A retry can reuse that record while its repository
retains it. The current split worker uses `InMemoryAiProcessingRepository`; records
do not survive process restart. This is not a durable outbox or an exactly-once
publication guarantee. PostgreSQL persistence occurs in the downstream dedup stage.

## Failure Statuses

AI worker persistence uses:

- `succeeded`: sent to post-AI dedup queue
- `manual_review`: valid parse but business confidence or other business
  checks failed. Common `failure_reason` values: `low_confidence`,
  `missing_essential_fields`.
- `failed`: retry/DLQ-ready processing failure. Common `failure_reason` values:
  `all_models_failed`, `ValidationError`, `LLMMalformedResponseError`,
  `storage_retryable`, `storage_permanent`.
