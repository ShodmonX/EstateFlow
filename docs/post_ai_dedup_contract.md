# Post-AI Deduplication Handoff Contract

## Queue

- Queue: `dedup.post_ai.structured_announcements`
- Payload schema version: `estateflow.announcement.v1`

Only successfully validated and normalized announcements are published here.
Malformed LLM output, storage failures, all-model failures, and low-confidence
manual-review records are persisted by the AI worker but are not sent to post-AI
deduplication.

## Idempotency

`idempotency_key` is inherited from the raw ingestion contract. Consumers must
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

Raw image binaries, temporary local paths, signed URL tokens, and storage
credentials are never included.

## Dedup Signals

`dedup_signals.media_phashes` is available for image matching. Phone presence is
only a weak helper signal and must never decide duplicates on its own.

`dedup_signals.media_phashes` comes from media pHash values. A pHash-only match
is an image similarity signal, not a complete duplicate decision.

## Processing Audit

AI worker persistence records:

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

The worker first persists a successful canonical record, then publishes to this
queue, then marks `post_ai_published=true`. If a worker restarts after
canonical persistence but before publish completion, retry republishes the
handoff once and marks the record published.

## Failure Statuses

AI worker persistence uses:

- `succeeded`: sent to post-AI dedup queue
- `manual_review`: valid parse but business confidence or other business
  checks failed. Common `failure_reason` values: `low_confidence`,
  `missing_essential_fields`.
- `failed`: retry/DLQ-ready processing failure. Common `failure_reason` values:
  `all_models_failed`, `ValidationError`, `LLMMalformedResponseError`,
  `storage_retryable`, `storage_permanent`.
