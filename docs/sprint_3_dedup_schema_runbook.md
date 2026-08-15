# Sprint 3 Deduplication And Schema Runbook

## Migration

`migrations/002_mvp_schema_and_post_ai_dedup.sql` is additive. It preserves
Sprint 1 tables and creates the MVP persistence surface for canonical
announcements, source children, media, users, filters, notifications, AI parsing
logs, processing jobs, referrals, source suggestions, flat audience tags, dedup
decisions, merge audit, and manual review.

`migrations/003_sprint3_schema_contract_adjustments.sql` is an evolutionary
follow-up. It adds source health metadata to `ingestion_sources`, makes
`announcements.is_promoted` nullable for future promoted-listing support, and
adds focused indexes for active source lookup, listener assignment, notification
delivery, source suggestions, referrals, and latest-source joins.

Downgrade is intentionally not encoded as an automatic destructive script.
Rollback that drops Sprint 3 tables can lose canonical listings, child/source
audit rows, review decisions, referral data, and source health history.
Production rollback should first export these tables or restore from a
point-in-time backup.

## Docs Diff

Current schema was compared against the decision order:

1. `docs/real_estate_changelog.md`
2. `docs/real_estate_v3.md`
3. `docs/real estate.md`

Implemented MVP deltas:

- v3.2 price fields: `price_period`, `price_basis`,
  `price_normalized_monthly`.
- v3.4 flat audience tags: `audience_tag_types` has no `parent_tag_id`; seed
  data excludes `young_family`.
- v3.5 R2/media storage surface: `announcement_media.storage_url`,
  `object_key`, pHash, SHA-256, dimensions, and metadata.
- Parent-child duplicate model: `announcements.parent_announcement_id`,
  `source_count`, `latest_source_id`, `dedup_decisions`, and
  `announcement_merge_audit`.
- Search contract: user-facing queries use only active parent/canonical rows.
- Future promoted listing fields are present but nullable:
  `is_promoted`, `promoted_until`.

Intentionally not implemented:

- `listing_type` column, because v3.2 leaves rent/sale classification as an
  open question.
- Audience hierarchy, because v3.4 removed it.

## Dedup Semantics

Post-AI dedup uses `estateflow.dedup.v1` weights:

| Signal | Weight |
|---|---:|
| source URL / forward provenance | 100 |
| same image pHash | 70 |
| district | 10 |
| rooms | 10 |
| area difference `<3 m2` | 10 |
| price difference `<5%` | 10 |
| floor | 5 |
| similar address | 15 |
| phone | 15 |

Phone is a weak helper signal. A phone-only match scores 15 and remains `new`;
it never creates a duplicate decision by itself.

Thresholds:

- `100+`: exact duplicate
- `80-99`: high-confidence duplicate
- `60-79`: possible duplicate, manual review
- `<60`: new

Price scoring compares only monthly-normalized total rental prices with matching
currency. One-time sale prices and per-person prices do not increase duplicate
score against regular monthly total rent.

## Canonical Query Contract

User-facing search must default to:

```sql
where parent_announcement_id is null
  and status = 'active'
```

Child/source announcements are retained for audit and admin views. They must not
appear as separate user-facing search results.

## Parent-Child Persistence

Default parent selection policy is `completeness_trust_first_seen`:

- higher canonical data completeness wins,
- then higher trusted source score,
- then earlier `created_at` / first-seen order,
- then stable announcement id as a final tie-breaker.

Exact and high-confidence duplicates are physically retained as child/source
announcements. The parent row is updated transactionally with `source_count`,
`latest_source_id`, and safe canonical aggregates. Existing trusted parent values
are not blindly overwritten by child values; collisions are written to
`announcement_merge_audit.conflicts`.

Persistent parent-child linking must lock the affected parent/child rows before
updating links. The asyncpg repository uses `FOR UPDATE`; the database also keeps
`announcement_merge_audit.child_announcement_id` unique so repeated processing of
the same child remains idempotent.

## MVP Performance Check

Candidate selection must use indexed cheap signals before scoring: source URL,
forward provenance, image pHash, district, rooms, price window, and recent time
range. The MVP target is to score at most 50 candidates per incoming structured
announcement.

MVP guardrails:

- candidate selection: one bounded candidate query, `limit <= 50`;
- dedup decision persistence: one insert into `dedup_decisions`;
- parent-child link: one transaction with row locks for affected parent/child
  rows, one child upsert, one parent aggregate update, one merge audit insert;
- user-facing search: one canonical query that filters out child rows;
- admin/audit search: explicit audit query may include child/source rows.

Representative migration smoke checks must apply all migrations to an empty
schema and insert at least one parent, one child, one media row, one referral,
one dedup decision, and one merge-audit row.

## Manual Review

Manual review statuses:

- `pending`: waiting for admin action;
- `approved`: accepted as a standalone/canonical-quality record or canonical
  fields were manually corrected;
- `rejected`: admin rejected the review item;
- `merged`: admin approved a duplicate relation.

Manual review reasons:

- `possible_duplicate`: score was between the possible threshold and automatic
  merge threshold **and** there is evidence-backed ambiguity (image/provenance,
  or corroborating address/description signals). Common fields alone (for
  example phone + district + room count) must not create a review item;
- `low_confidence`: schema-valid AI output had low business confidence;
- `business_quality`: schema-valid data still failed a business-quality check.

The v2 dedup policy treats phone + matching district, rooms, area, normalized
monthly price, and floor as a strong structured fingerprint and merges it
automatically. This avoids filling the manual queue with repeated listings from
the same realtor while keeping weak/generic text matches as new announcements.

Admin actions are idempotent service calls intended to be wired into the Sprint 6
admin panel: mark as new, merge duplicate, update canonical fields, and reject.
Every action appends an audit event.
