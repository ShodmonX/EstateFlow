# Sprint 6 Ops Handoff

Status: ready for Sprint 7 beta control-plane testing.

## Admin Role Provisioning

- FastAPI admin endpoints are protected with `X-Admin-Token`.
- Set `ADMIN_API_TOKEN` in the deployment secret store before exposing `/admin/*`.
- Do not print or paste the token in support chats, logs, screenshots, or runbooks.
- Telegram admin actions must pass a positive `admin_user_id`; `0`, missing, or non-admin flows are treated as unauthorized.
- Every admin action must include an `idempotency_key` and, when acting on pending queues, an `expected_status` precondition.

## Pending Queue Monitoring

Check these queues at least twice daily during beta:

- `/admin/source-suggestions/pending`
- `/admin/audience-tags/pending`
- `/admin/manual-review/pending`

Recommended filters:

- Source suggestions: oldest first; approve only after manual channel validity checks.
- Audience tags: review new candidates, normalize synonyms, merge duplicates into existing canonical tags.
- Manual review: prioritize `possible_duplicate`, then `low_confidence`, then `business_quality`.

Empty queues are healthy. Growing queues mean beta data is outpacing admin capacity.

## Failed Publish Retry

- Scheduled content posts are persisted in `content_channel_posts`.
- Terminal statuses for daily cap are `drafted`, `published`, and `dry_run`.
- `failed` rows keep `attempts`, `next_attempt_at`, and `error_reason`.
- Retry only after `next_attempt_at`; use the same idempotency key.
- If Telegram credentials are missing, publisher falls back to dry-run and no real channel post is sent.
- Daily automated posts must remain capped at 1-2 posts.

## Tag Merge Impact

- Audience tags are flat; `parent_tag_id` is not allowed.
- `young_family` and `yosh_oila` normalize to `family`.
- Pending/rejected tags must not be attached to canonical listing filters.
- Merge is transactional in the DB repository: existing `announcements.audience_tags`,
  `announcements.audience_excluded_tags`, and saved filter criteria are remapped to the target tag.
- Hard delete is not part of the policy; merged tags become `rejected` and audit history remains immutable.

## Source Approve Rollback Policy

Approving a source suggestion creates or enables one source through the source registry and grants one week of Premium exactly once.

If a source was approved by mistake:

- Disable the source in the source registry/listener config; do not delete historical announcements.
- Stop or remove listener assignment only after the source is disabled.
- Record a follow-up admin audit note with the reason.
- Do not revoke already granted Premium unless fraud is confirmed by a separate admin policy decision.
- Do not re-run approve with a new idempotency key to "fix" the source; use explicit disable/rollback tooling.

## Beta Readiness Checklist

- Admin token provisioned in secret storage.
- Pending queue review owner assigned.
- Content channel credentials present only in production, otherwise dry-run is expected.
- Failed publish retry job monitored.
- Source approval and tag merge decisions are reviewed in audit logs.
- Search filters are validated against approved canonical semantics, not pending AI candidates.
