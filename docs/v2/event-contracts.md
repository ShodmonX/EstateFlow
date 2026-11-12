# V2 contracts — Task 001 draft

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See verification/FINAL_V2_REPORT.md and decisions/ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **DRAFT / PROPOSED except explicit platform constraints.** No runtime, topology or Task 002 authorization. Machine schemas: contracts/v2/schemas/*.json. Synthetic scenarios: tests/fixtures/v2/contracts/*.json. Draft schema IDs use contracts.estateflow.invalid; this is an offline namespace, not a remote registry.

## Evidence and superseded assumptions

VERIFIED V1: estateflow.events is durable DIRECT (`src/estateflow/services/queue.py:175–178`); envelope is a dict-bearing dataclass (`src/estateflow/contracts/events.py:15–27`); repeated edits use one key (`src/estateflow/services/telegram_listener.py:467–473`); runtime drops empty deletes (:251–252). Candidate hash depends on text/index/recipe (`src/estateflow/services/source_parsing/contracts.py:364–403`); persisted event is incomplete (`src/estateflow/services/post_ai_dedup.py:1458–1476`). All V2 behavior below is a proposal, not a claim these defects are fixed.

The prior same-name TOPIC proposal is superseded: **estateflow.v2.events TOPIC**, durable, separate from **estateflow.events DIRECT** and its V1 DLX. No in-place redeclaration or implicit transfer of V1 queues. The user requires a distinct V2 exchange; queue/retry operating choices below await review.

## Envelope and versioning

Every body has event_id (UUID), event_type (without version suffix), event_version (major), producer (owning context), occurred_at (reported business time), recorded_at (owner journal time), correlation_id (trace), causation_id (upstream event or null at ingress), aggregate_kind/id/version and a per-event closed payload. Routing key is event_type + `.v` + event_version. AMQP message_id must equal event_id; correlation_id is never an entity/event identity.

Schemas use JSON Schema Draft 2020-12. envelope.v1.schema.json constrains shared fields; event schemas constrain type, producer, aggregate kind and payload. Validate the specific event schema, not the envelope alone. Only types.v1 and scenario.v1 are shared definitions/fixture annotation. Production must select schema by routing key plus envelope and reject disagreement, unknown producers or major versions. Broker publisher ACLs/authorization must additionally enforce producer ownership; a producer string is not authentication.

IDs are UUIDs assigned once and durably retained, including imported V1 ID aliases. Immutable IDs do not imply deterministic hashing from mutable text. Version counters are positive integers local to the accountable stream. Generic stream key is (producer, aggregate_kind, aggregate_id); never compare pre_ai and ai versions as a global sequence. Required equality/lineage checks are **domain invariants outside JSON Schema**: envelope aggregate ID/version must equal message_uid/source_revision, album_id/album_revision, candidate_id/candidate_version, batch_id/generation, media_job_id/media_revision or canonical_id/canonical_revision, respectively. The draft schemas fix candidate_version and candidate-event aggregate_version at 1; a changed candidate requires a new generation/ID, not an in-place mutation. New candidate stages carry the same candidate identity/version; their owner stage records and event IDs differ. A reused event_id with a different body hash is corruption/security failure, not an idempotent retry.

Each frozen schema file has immutable $id and a versioned filename. Closed payloads reject unknown properties. Therefore arbitrary additive wire fields are **not automatically compatible** with these draft validators: reserve explicitly documented optional fields, or coordinate a reviewed schema revision/consumer rollout; otherwise publish a new major event version and run old/new contracts side by side. Do not overwrite a released schema ID. These files are unreleased drafts. No compatibility with V1 Python event payloads is claimed.

## Source, album, candidate and canonical identity

| Identity | Definition and version | Responsible owner |
|---|---|---|
| source_id | Stable subscription/source identifier; native channel is an attribute/alias, not a UUID hash of a mutable username | listener |
| message_uid | Stable source message evidence ID; immutable mapping from platform/channel/native message ID. Reconnect/catch-up/import retains this mapping | listener |
| source_revision | Durable per-message observation/state counter under a fenced owner; NOT a globally ordered Telegram revision. Edits get new counters/event IDs; duplicate recognized transport observation reuses event_id | listener |
| album_id / album_revision | Stable source/channel/native group mapping; revision for each membership/content change, including late member and member tombstone | listener |
| batch_id / generation | Immutable parse execution generation identity; generation ordered within source/album lineage, assigned durably by parser; batch_id never reused with different policy/input | source_parser |
| candidate_id / candidate_version | Immutable item within one generation, not a digest position used as canonical identity. Current draft candidate_version is 1; changed input/policy produces new generation/candidate IDs | source_parser |
| canonical_id / canonical_revision | Actual stored listing identity and monotonic catalog transaction revision; edits/media/review/merge/evidence changes increment it | catalog |
| media_job_id / media_revision | One durable job identity for a candidate/source revision; repeated attempts retain job, successful/failed/unavailable state changes increment media revision | ai (initial AI+Media context) |

Source references are immutable scalar IDs and observed revisions copied into owner-local projections. Native message IDs remain intact in raw payloads; digest slot_key is a lineage aid, never a replacement native ID. Catalog retains immutable candidate-to-source and candidate-to-canonical evidence mappings, so a retraction needs no read from listener/parser schema. Catalog IDs are never re-derived from a source idempotency key.

## Lifecycle and ordering rules

1. Create → edit1 → edit2 → delete: journal distinct revisions and business event IDs. Raw creates/edits carry content digest, bounded text and opaque media capture references. Delete carries source identity/revision/reason and no text/media bytes. Raw delete and catalog evidence retraction bypass extraction and cheap dedup. Pre-AI suppression does not discard new source-evidence lineage: duplicate decisions include duplicate_of_candidate_id and catalog must attach/reconcile that evidence.
2. observed_order=confirmed is an assertion by the fenced Listener backed by a persisted transport token or explicit owner reconciliation; timestamps alone cannot justify it. If native ordering cannot be established, label uncertain, persist/park and reconcile before applying potentially stale content. The local source_revision orders journal decisions, not truth about Telegram history. Exact transport dedup token mapping/catch-up protocol remains OPEN D03; ambiguous repeated identical observations must not be silently erased.
3. On raw version gap, durably park and request owner history/reconciliation. ACK only after durable parking. Equal revision with same hash is duplicate; different hash is conflict; lower revision ignored only after tombstone/history checks. Delete-before-create retains tombstone watermark and prevents lower-version creates from resurrecting evidence. Proposed terminal delete within message identity requires explicit correction/reinstatement command with evidence if deletion was mistaken (D03); do not treat delayed edit as reinstatement.
4. Listener persists each album member before settling. Settled is a bounded assembly snapshot, not a guarantee of no future members. Late member increments album revision and triggers a new parser generation; edits/deletes update member evidence and assembly. Consumer receives full membership, original message IDs available from its local raw projection. Buffer window, memory/byte/member caps and expiry outcome are OPEN D04; proposed durable quarantine on expiry, no silent fail-open.
5. Parser binds each run to a policy_id/revision, recipe key/version, config SHA256 and immutable private snapshot_ref. Replay uses original snapshot; reparse is an authorized new command/generation with original source lineage plus explicit new policy. No copying real configuration or regex into public events; policy snapshot is served to parser through its own history/projection. Listener binds source transport to policy snapshot through parser/control events, not cross-schema SQL.
6. Digest fanout emits candidates and parser.batch.completed with full candidate set, supersedes_batch_id and explicit retracts_candidate_ids. Manifest/candidate arrival can reorder: persist and wait until complete candidate set exists and all accepted extraction/review outcomes needed for activation exist before atomic generation activation. A many-to-one or uncertain cross-generation canonical mapping requires catalog review, not an implicit merge. Quarantined/buffered outcome is not evidence that all prior candidates should be erased; candidate/retraction sets are empty and previous approved batch stays until reviewed. Failed or partial generations cannot activate half a replacement. Cross-generation matching is explicit supersedes_candidate_ids, never position alone. A full successful zero-candidate parse may retract prior evidence only under ratified D03 policy. Partial quarantine retains old generation until operator approval or a reviewed partial-activation policy.
7. Catalog maintains all active/retracted evidence, recomputes canonical fields with provenance, then emits a full authoritative snapshot. Catalog stores per-message tombstone watermarks and generation invalidations. Late precheck/extraction/media results referencing a retracted or superseded source revision cannot reactivate evidence. Removing one source does not remove remaining active sources. Proposed last-source behavior is hidden/retracted, retaining canonical ID/audit; alternate manual hold requires approval D03. Tombstone is durable state, not physical deletion. Privacy erasure is a distinct authorized workflow and retention decision D07.
8. Manual review pending/approved/rejected and merges are catalog mutations with new revisions. Merge keeps child IDs as redirects, parent ID stable, attaches evidence and publishes changed parent plus retracted/redirect child from one local transaction/outbox, with shared mutation_id and related_canonical_ids. Delivery across aggregate queues is not atomic: projectors preserve redirect and reconcile related snapshot gaps. UI may show pending convergence. Unmerge is a new compensating catalog operation, not event history rewriting; exact product permissions remain OPEN.
9. Media is explicit unavailable/pending/ready/failed. Ready carries immutable storage key/hash/mime/dimensions; no signed URL, binary bytes, session or access_hash. Failed carries reason code; stale results for older candidate/source revisions cannot overwrite current media. Retries reuse job and later state revisions. A catalog media change creates a catalog revision/event. D05 decides whether active publication waits for ready, supports text-only or uses a time-bounded partial state. Schema representation alone does not approve a visibility policy.
10. Catalog snapshots include actual canonical ID, revision, visibility, review state, parent redirect, all source evidence, listing and media state. Search/notifications/content store copies and apply newer complete snapshots; lower versions ignored, equal with different hash quarantined. A gap can be applied for a complete snapshot while recording reconciliation lag; never run delta commands using this shortcut. Notifications must not infer a new send from every revision/replay. Match/send identity is a domain decision (D08); proposed one initial user/filter/canonical notification plus explicitly opted-in update alerts.

## Immutable body, redelivery and replay

New business mutation → new event_id, causation chain and owner revision. Redelivery/retry → identical body/event_id, changed delivery attempt headers only. Authorized replay → original body and event_id retained, new replay_operation_id, actor_ref, reason, scope, checkpoint and mode in authenticated **delivery metadata**, audited in replay owner's store. Synthetic scenario delivery fields document this metadata; they are outside event bodies. Do not let an untrusted replay header bypass inbox/authorization.

Recovery replay uses existing inbox dedup and repairs pending effects only. Projection rebuild uses a new isolated projection/inbox namespace, preserves original events and **prohibits external sends**. Explicit reparse uses a new parser command/batch/generation and new downstream business event IDs, with original source evidence referenced. A delivery-unknown resend is a separately authorized delivery attempt/command, never a generic replay side effect. Media references/policy snapshots must still exist within replay retention; missing/expired references park explicitly rather than fabricate success. Retention longer than inbox/tombstone history is unsafe; D07 must align them.

## Core event producer/consumer and queue catalog

Accepted candidate.prechecked bodies include lineage, text and media capture references so AI does not fetch a pre-AI/parser database row; rejected duplicates are consumed by catalog for evidence reconciliation. Each context resolves opaque references only through its authorized owner-local projection/capability interface.

Every row below has a machine schema named `<event_type>.v1.schema.json`. Routing key is `<event_type>.v1`. Multiple independent consumers have separate durable queues; workers within one consuming domain share work only deliberately.

| Event type | Producer | Consumer-owned queues / effect |
|---|---|---|
| raw.message.received / raw.message.edited | listener | v2.source_parser.raw.v1 (parse local source projection) |
| raw.message.deleted | listener | v2.source_parser.raw.v1 (invalidate/batch lineage); v2.catalog.sources.v1 (retract all linked evidence, no AI) |
| source.album.changed | listener | v2.source_parser.albums.v1 (new generation once raw members present) |
| parser.candidate.parsed | source_parser | v2.pre_ai.parsed.v1 (persistent signals/precheck) |
| parser.candidate.rejected | source_parser | v2.control.parser.v1 (private review operation), v2.analytics.parser.v1 (reason/count projection, restricted/redacted transport) |
| parser.batch.completed | source_parser | v2.catalog.parser.v1 (complete replacement manifest), v2.pre_ai.parser.v1 (invalidation coordination) |
| candidate.prechecked | pre_ai | v2.ai.prechecked.v1 (accepted extraction only); v2.catalog.prechecked.v1 (duplicate evidence lineage) |
| candidate.extracted | ai | v2.catalog.extracted.v1 (catalog transaction/review) |
| media.unavailable / media.ready / media.failed | ai | v2.catalog.media.v1 (readiness revision); v2.ai.media.v1 (owner-local extraction orchestration) |
| catalog.listing.changed / catalog.listing.retracted | catalog | v2.search.catalog.v1; v2.notifications.catalog.v1; v2.content.catalog.v1; v2.analytics.catalog.v1; v2.control.catalog.v1. Each maintains its own projection/inbox |

No wildcard binding implicitly authorizes full raw text/contact access. Analytics/control consumers of richer snapshots must have a privacy-reviewed transformation/field-minimized projection before production. Alternative is explicit owner-published minimal metric/audit event; D07 must choose, and these draft bindings are not production ACL approval.

## Additional domains and commands (document-only interfaces)

The following names and payload obligations are **PROPOSED / schemas deferred**, so their producers must not publish to production under this draft. Event versions are `.v1`; context commands use `<owner>.command.<action>.v1`, operation results use `<owner>.operation.completed/failed.v1`. Commands specify command_id, operation_id, actor_ref/authorization scope, target ID, expected_version, idempotency_key and domain-specific body. Result carries command_id/operation_id, result revision, safe reason and status. Outbox/inbox rules apply equally to commands. Client retry reuses command ID/key; expected-version conflict returns an operation result, not an endless transient retry.

| Producer → consumer | Event/command namespace | Queue / obligation |
|---|---|---|
| adapters/control → identity | identity.command.upsert_user / activate_referral / grant_premium | v2.identity.commands.v1; reward once by originating suggestion/activation ID |
| identity → notifications/control | identity.user.changed; identity.entitlement.changed | v2.notifications.identity.v1, v2.control.identity.v1; scalar user ID, entitlement revision/expiry/status, no profile PII needed for matcher |
| adapters → notifications | notifications.command.save_filter / delete_filter / set_consent | v2.notifications.commands.v1; owner validates user auth and expected filter/consent version |
| notifications → identity | notifications.filter.activated | v2.identity.filters.v1; eligibility fact, not authority to modify premium directly |
| adapters → control | control.command.suggest_source / approve_source / set_flag | v2.control.commands.v1; operation state/saga, admin audit |
| control → listener/source_parser | listener.command.set_subscription; source_parser.command.set_policy / reparse / release_quarantine | v2.listener.commands.v1, v2.source_parser.commands.v1; transport/policy owners acknowledge before activation |
| listener/source_parser → control and local projections | listener.subscription.changed; source_parser.policy.changed; owner operation results | v2.control.sources.v1, v2.listener.policy.v1; exact immutable binding revision and source descriptor |
| control → all affected contexts | control.flag.changed | separate v2.<owner>.control.v1 queue per affected owner; revisioned rollout acknowledgment, no shared flag table reads |
| adapters → taxonomy | taxonomy.command.approve_tag / remap_tag | v2.taxonomy.commands.v1; record alias and remap operation |
| taxonomy → catalog/notifications/ai/search/content | taxonomy.tag.changed / taxonomy.tag.remapped | v2.<owner>.taxonomy.v1 for each listed owner; remap version, old/new stable tag IDs, aliases; owners acknowledge |
| adapters → catalog | catalog.command.review / merge / retract / reinstate | v2.catalog.commands.v1; expected revisions and authorization; full catalog snapshots follow |
| adapters/control → content | content.command.schedule / publish / resolve_unknown | v2.content.commands.v1; send policy and actor audit; no automatic unknown resend |
| content → control/analytics | content.publication.changed | v2.control.content.v1, v2.analytics.content.v1; attempt/outcome; rendering reads local projection |
| notifications → control/analytics | notifications.delivery.changed | v2.control.delivery.v1, v2.analytics.delivery.v1; sent/failed/unknown, no raw recipient/text |
| each owner → analytics/control | <owner>.metric.recorded; <owner>.audit.recorded | v2.analytics.<owner>.v1 / v2.control.audit.<owner>.v1, provisioned per producer; minimal allowlisted facts, owner-local authoritative audit |
| each command owner → originating operation tracker | <owner>.operation.completed / failed | v2.control.operations.v1 or dedicated adapter-owned operation projection; authenticated requester scope, durable status |

Queues for document-only contracts may be specified for implementation planning, but are not created in Task 001. Per-owner event/command schema completion and authorization tests are gates before that domain is extracted. Synchronous UX choice D06: recommend asynchronous accepted/operation polling; request/reply alternative has latency and timeout ambiguity and needs separate approval. Query HTTP from an owner's own projection is allowed; business mutation never writes another owner's DB.

## Foundation durability and failure matrix (specification only)

Topology provisioner acting for consumers declares exchanges/queues/bindings and narrow publish/consume permissions. Producers do not declare foreign consumer queues. Durable messages, explicit publisher confirms, mandatory routing/returned-message handling, manual ACK and reconnect recovery required. If an event has no intended binding, publish remains pending/alerts; confirm alone cannot establish consumer processing.

Owner transaction writes domain state/revision + outbox; dispatcher leases rows and publishes with stable event_id; confirm then sent-mark. Consumer validates specific schema and authorization, then atomically commits inbox (consumer namespace + event_id + body hash), effects and outgoing outbox before ACK. Multiple projections within a domain use distinct inbox namespaces. External send cannot share that transaction: notifications/content persist intent/lease, attempt and unknown outcome separately. No network call while holding a long database transaction.

Proposed retry choice D02: owner-local persistent scheduler, with `min(cap, base*2^attempt)` and bounded jitter, fixed max attempts and max age, stable event body/ID, explicit due time. Alternative broker TTL/DLX tier queues reduces DB scheduling but introduces head-of-line/tier/operating complexity; delayed plugin adds deployment dependency. Proposed illustration base=2s, cap=60s, 5 retries after initial attempt is **not approved**. Due schedulers distinguish transport retry, domain retry and external send ambiguity.

Proposed durable V2 DLX estateflow.v2.dlx DIRECT, each consumer queue has `<queue>.dlq`; park unsupported schema/authorization/conflict with private payload reference and redacted diagnostic (never raw text/secret). Retry queues, if selected, use the V2 namespace only. ACK invalid work only after durable parking or confirmed broker rejection path; no silent drop. Authorized replay is consumer-owned, scoped and auditable. Outbox dispatcher deployment (embedded versus sidecar) and broker queue replication type remain OPEN D02.

| Fault scenario | Required result / proof before Task 002 gate |
|---|---|
| Producer dies before/after DB commit | Domain+outbox both absent or both present; restart eventually publishes |
| Confirm succeeds then dispatcher dies | Duplicate event publication, inbox yields one local effect |
| Consumer dies before commit / after commit before ACK | Redelivery gives one committed effect plus outgoing outbox |
| Republish before ACK / reconnect | Stable event ID/body, no lost retry, duplicate safe |
| Unknown schema / wrong route / bad UUID / forbidden field | Durable park, safe diagnostic, no business effect; negative fixtures |
| Source edits reorder / uncertain timestamp | Park gaps/uncertain history, retain tombstones; no invented global sequence |
| Digest batch/candidates reorder / quarantine | Wait for complete approved generation, no partial retraction |
| Catalog merge snapshots reorder | Child redirects and parent evidence converge using mutation links |
| Media result arrives after reparse/delete | Reject stale association, no media resurrection |
| Due send claimed twice / process killed before or after send | One lease; explicit unknown state and approved recovery, no exactly-once claim |
| Projection replay vs recovery | Fresh projection namespace rebuilds; no external sends |
| Cross-owner SQL/role escalation | Negative grants/default privileges/sequence tests fail access as intended |

## Schema scope, fixtures and open choices

Core listing schema intentionally models only draft price/basis/rooms/district/tags/description/contact_ref and media references. **TODO before production extraction:** map V1 area/floor/renovation/furniture/confidence/assumptions/normalized pricing/source URLs and complete provenance with field-by-field parity and privacy review. No arbitrary bag accepts these silently; closed schema currently rejects them. media_capture_refs/policy snapshot_ref/contact_ref are opaque bounded strings; **TODO** capability namespace, resolution authorization/expiry and private retention schema. This is a deliberate Task 001 draft limitation, not a complete production catalog contract. Task 002 foundation can use synthetic subset only; full domain extraction is blocked.

Scenario files carry schema_valid and consumer expected action separately. Negative schema fixtures test structural rejection; semantic mismatch fixture deliberately passes JSON Schema and must be parked by a future domain validator. Sequential outcomes are assertions/specifications, not executed business tests. Cases cover two edits/delete, delete-before-create, duplicate/reorder/uncertain ordering, late album, digest reparse and replacement manifest, quarantine/buffer, precheck/extract, review/merge/multi-source retraction, media unavailable/ready/failed, same-body recovery replay, isolated rebuild and invalid inputs.

Open decisions, recommendations, alternatives and approval impact are centralized in task-001-decisions.md (D01–D08) and ADRs. Do not infer acceptance from a schema-valid example or a recommended default.
