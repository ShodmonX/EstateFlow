# Pre-AI Deduplication Contract

## Decision Types

- `proceed_to_ai`: no useful duplicate signal was found.
- `needs_reviewable_signal`: weak or ambiguous signals were found, but the event
  still proceeds to AI.
- `high_confidence_duplicate`: AI may be skipped because an exact, auditable
  duplicate reference was found.

Only `high_confidence_duplicate` sets `should_skip_ai=true`.

## High-Confidence Skip Rules

AI can be skipped only for:

- Exact forward origin match: original channel ID + original post ID.
- Exact normalized text SHA-256 match.

Every skip decision must carry an `audit_reference` pointing to the matched raw
event or parent reference.

## Weak Signals

These signals are collected for audit, tuning, and post-AI dedup handoff, but
they never skip AI by themselves:

- Near-text Jaccard match.
- Media pHash similarity.
- Same phone number.

Phone numbers are weak helper signals only. One agent may reuse one number across
many unrelated listings, so same-phone-only events remain eligible for AI.

## Thresholds

Configured through environment/settings:

- `PRE_AI_NEAR_TEXT_THRESHOLD`
- `PRE_AI_PHASH_HAMMING_THRESHOLD`

The default implementation emits a sanitized Ops metric for every decision with
the decision type, source ID, signal count, and correlation ID. It does not log
raw listing text or phone numbers.
