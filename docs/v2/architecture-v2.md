# EstateFlow V2 — reconciled architecture draft

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See verification/FINAL_V2_REPORT.md and decisions/ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **DRAFT / Task 001 review required. Only Task 001 documentation and contract design authorized.**

Variant A (one PostgreSQL instance/database), RabbitMQ business communication and incremental extraction are confirmed platform choices. AI/media initial co-deployment and one notifications owner are explicit user constraints. Other domain allocations and operational/product policies below are proposals. Phase 0 evidence remains in architecture-audit.md and its inventories; no draft statement overrides source evidence.

## Bounded contexts and process boundaries

| Accountable context / schema | Owned state and responsibilities | Inputs / outputs (draft) |
|---|---|---|
| Listener / listener | Transport subscriptions, fenced session lifecycle, ingress journal, source revisions, album membership | Subscription commands → raw.message.received/edited/deleted, source.album.changed |
| Source Parser / source_parser | Versioned recipes, policy snapshots, candidate generations, buffers, private quarantine | Raw/album/policy commands → parser.candidate.parsed/rejected, parser.batch.completed |
| Pre-AI / pre_ai | Durable cheap signals, suppression decisions and stage inbox | Parsed candidates → candidate.prechecked; tombstones bypass suppression |
| AI+Media / ai | Extraction runs, prompts, media jobs/storage transformations; restricted media session access | Accepted prechecks → candidate.extracted, media.unavailable/ready/failed |
| Catalog / catalog | Canonical listings, evidence, parent/child merge, review, dedup decisions and attachment references | Extracted candidates, parser generation manifests, source retractions, media and taxonomy events → catalog.listing.changed/retracted |
| Search / search | Rebuildable query projection including visibility, tags and media | Catalog snapshots → local query endpoint; never catalog SQL |
| Notifications / notifications | Saved filters, matching projections, jobs, due schedule, sending leases and attempt ledger | Catalog/identity/taxonomy events, filter commands → local send workflow and status events |
| Identity / identity | Users, referrals, premium entitlements and awards | User/referral/premium commands, filter activation fact → entitlement/profile changes |
| Control / control | Flags, source suggestions/approval orchestration, command tracking, central admin audit projection | Admin/source suggestions → owner-directed commands; consumes audit/results |
| Taxonomy / taxonomy | Audience tag definitions, aliases and remap workflow | Approve/remap commands → versioned taxonomy changes, owner acknowledgments |
| Content / content | Scheduled posts, rendering projection, publication ledger and unknown sends | Catalog/analytics projections, scheduling commands → publication status |
| Analytics / analytics | Product and technical event projections, metrics | Owner events → aggregate metrics; no shared insert table |

Matcher/sender are separate processes within notifications, not separate table owners. AI+media is one initial deployment and schema; catalog continues to own attachment references. A later media split requires a session-free AI contract, bounded media fetch capability, real codec correctness, storage retention and readiness/latency proof. Splitting notification ownership additionally requires proved due scheduling, atomic claims, lease recovery and an approved unknown-send policy. Separate worker scaling inside the domain can precede such a split.

## APIs, bot and command ownership

Public/admin/internal HTTP and Telegram bot are ingress/query adapters. Their process names do not confer database ownership. Prefer domain-owned API handlers or adapters with owner-local read projections and command tracking; adapter auth/session state must have an explicit owner (identity for user auth, control for admin command tracking). No adapter gets all schema credentials. UI polling can use the adapter's own operation projection.

| User/admin action | Accountable owner | Cross-context consequences |
|---|---|---|
| Authenticate/upsert user; activate referral; grant premium | identity | Entitlement event feeds notifications; reward command from control idempotent by suggestion ID |
| Create/update/delete saved filter; notification consent | notifications | Filter-activated fact triggers identity referral eligibility; no shared transaction |
| Search/listing detail | search | Reads its local catalog projection; exposes projection revision/lag |
| Suggest/approve source | control | Orchestrates listener subscription and parser binding commands; reports per-owner pending/failed state |
| Configure session/subscription | listener | Session fencing and transport authorization; parser binding snapshot acquired through events |
| Set recipe/reparse/release quarantine | source_parser | Authorized reparse creates new generation, not forged raw history |
| Approve/reject review, merge or retract canonical listing | catalog | Transactional snapshots update search/notifications/content |
| Approve/remap tag | taxonomy | Catalog and notifications apply local remaps, acknowledge operation |
| Change feature flag | control | Owner-local projections consume revision; acknowledgments identify rollout lag |
| Schedule/publish content | content | External send outcome tracked; no synchronous catalog SQL |
| Inspect metrics/audit | analytics / control respectively | Private projections with retention; owners retain authoritative audit |

Cross-owner mutation uses RabbitMQ commands with operation_id, target owner, expected version, authenticated actor reference and idempotency key (document-level draft; command schemas are deferred). Proposed UX is 202/operation_id plus polling an owner-fed operation projection. Request/reply with timeout is an OPEN alternative, not an approved default; neither timeout nor adapter disconnect cancels a committed owner command. Consumers authenticate producer/actor authorization; arbitrary routing cannot authorize a command. See event-contracts.md D06.

## Evidence and changes from initial draft

VERIFIED V1: direct exchange declaration `src/estateflow/services/queue.py:175–178`; notification shared reads/writes `src/estateflow/repositories/sqlalchemy.py:1080–1300`; media session dependency `src/estateflow/services/media_storage.py:164–200`; broad API runtime construction `src/estateflow/api/app.py:38–67`. The old chart's independent media and notification-matcher/delivery data owners, APIs as unspecified owners, and incomplete happy-path event flow are superseded by this draft. Source lifecycle ordering and policy decisions are specified in event-contracts.md; every legacy table and T0–T6 transition is in database-ownership.md.

## Isolation, rollout and rollback

No cross-owner SQL, FKs, ORM/repository imports or shared business transactions in migrated components. Contracts are JSON Schemas, not shared Python business entities. Owner-local outbox/inbox, bounded retries, safe diagnostics and per-aggregate versions are required. Raw media bytes and sessions never travel in events. Source tombstone does not mean merged canonical deletion. Exactly-once Telegram notification delivery is not promised.

The new durable TOPIC exchange is estateflow.v2.events; V1 estateflow.events remains DIRECT, including its DLX/bindings. Shadow consumers cannot send notifications/content or open duplicate live listeners. Backfill/checkpoint and reconciliation precede each single-writer cutover. Retain V1 data/routes until a reviewed rollback window expires; reverse routing only after reconciling newer writes. No deployment or migration is authorized by this document.
