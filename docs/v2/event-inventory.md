# Event and state inventory — Task 000

> 2026-11-10 lab implementation addendum: original V1 evidence below is retained. The as-built lab uses owner-local JSON records and one exact-bound consumer queue per owner, documented in decisions/ADR-006-isolated-v2-as-built.md and verification/FINAL_V2_REPORT.md. The26-table importer is archive-only; no physical V1 table migration or active-entity compatibility is proved.

Audit date 2026-11-09; commit `addb856a99d24946ef127384121b99de371c017b`. All current implementation facts below are VERIFIED from source. Deployment topology and crash behavior on a real broker are NOT VERIFIED.

## Current RabbitMQ topology

`src/estateflow/services/queue.py:145` defaults to `estateflow.events`, declared durable **DIRECT** at :175; durable direct `estateflow.events.dlx` at :180. Each queue binds using its own name as routing key (:195–208). Producer calls also create queues and DLQs (:214), so ownership is shared. Queue declarations are lazy; EVENT_QUEUE_NAMES (:39) is not an eager provisioning step. Persistent JSON messages use correlation_id as message_id (:216–224). Channel construction does not explicitly set publisher_confirms (:171); library default behavior is NOT VERIFIED here. Do not claim confirms are absent or that confirms establish an outbox.

| Exchange / routing key = queue | Producer | Consumer | Payload and evidence |
|---|---|---|---|
| estateflow.events / ingestion.raw_announcements | Listener QueueRawTelegramEventPublisher | worker_pre_ai | RawTelegramEvent with source IDs, text, update type, media references, parser binding snapshot; `src/estateflow/services/telegram_listener.py:503`, `services/worker_pre_ai/main.py:41` |
| estateflow.events / ingestion.source_parser.quarantine | PreAiIngestionProcessor | No deployed consumer found | Full raw event plus parser outcome, rejected segment and stable quarantine ID; `src/estateflow/services/ingestion_pipeline.py:133–164` |
| estateflow.events / ai.processing.raw_announcements | Parser/pre-AI | worker_ai | Raw candidate plus parsing lineage and dedup signals; `src/estateflow/services/ingestion_pipeline.py:180–214`, `services/worker_ai/main.py:55` |
| estateflow.events / dedup.post_ai.structured_announcements | AI/media | worker_dedup | Canonical listing, stored media, parser provenance, manual review reason; `src/estateflow/services/ai_worker.py:496–532`, `services/worker_dedup/main.py:46` |
| estateflow.events / announcement.persisted | PostAiDedupProcessor | worker_notification_match | Partial listing snapshot; `src/estateflow/services/post_ai_dedup.py:1458–1476`, `services/worker_notification_match/main.py:60` |
| estateflow.events / notification.delivery | RabbitNotificationPublisher | worker_notification_delivery | NotificationJob serialization; `services/worker_notification_match/main.py:24–38`, `services/common/notification_payload.py:1`, `services/worker_notification_delivery/main.py:43` |
| estateflow.events / content.channel.scheduled_posts (if invoked) | ContentScheduleService | No split deployment consumer found | kind, day, timezone, idempotency key; `src/estateflow/services/content_automation.py:15`, :398–425; processing method :267 exists, but no dedicated runtime wiring found |

For every queue above, `<queue>.dlq` binds to `estateflow.events.dlx` using the original queue name (`src/estateflow/services/queue.py:204`). No automatic DLQ/quarantine replay dispatcher was found in these runtimes. `scripts/replay_media_sample.py` is a media utility, not a general event replay service.

`notifications.standard` and `notifications.high_priority` are logical job classifications (`src/estateflow/services/notifications.py:27–41`), carried inside the single notification.delivery queue payload (`services/common/notification_payload.py:21`). They are not separately bound premium broker queues in the split publisher.

## ACK, retries, ordering and identity

- Manual delivery uses queue.get(no_ack=False), JSON decode and an in-memory delivery token map (`src/estateflow/services/queue.py:229–249`). Malformed input is rejected without requeue. Broad exceptions also return None, making broker errors look like an empty poll.
- `RabbitStageConsumer` calls handler, retries raised exceptions, ACKs normal return (`src/estateflow/services/stage_consumer.py:43–57`). Domain failures returned as values are therefore ACKed unless the entrypoint explicitly raises. AI entrypoint escalates all-model and retryable storage failures (`services/worker_ai/main.py:65–71`); delivery does not (:43–44 in its entrypoint).
- Retry increments body metadata, republishes immediately, then ACKs original; exhausted attempts reject to DLQ (`src/estateflow/services/queue.py:258–279`). No TTL delay/backoff or jitter exists here. A crash after retry publication before ACK duplicates work. Ordering can change across retries and multiple consumers.
- Envelope is an unvalidated dataclass containing dict payload, correlation ID and string metadata (`src/estateflow/contracts/events.py:15–27`). It lacks event_id, causation_id and aggregate_version. Some nested domain structures validate through Pydantic; there is no uniform envelope/schema gate.
- Legacy combined consumer has different failure handling and polls due notifications (`src/estateflow/services/queue_consumer.py:60–65`). Its runtime builds a post-AI processor without event_queue and a matcher with a no-op publisher (`src/estateflow/application/runtime.py:275–289`). It is not a parity reference for split broker behavior.
- `announcement.persisted` omits canonical announcement_id, source_url, parent/status/source_count/version and complete lineage (:1464–1472 in post_ai_dedup.py). Consumer reconstructs UUID5 from idempotency key, defaults status active and loses omitted attributes (`src/estateflow/services/post_ai_dedup.py:85–119`). If DB source conflict resolves to an existing ID, reconstructed ID can disagree. This last production scenario is SUSPECTED; information loss in the serializer is VERIFIED.

## Redis and memory

| State | Current location / persistence | Restart/concurrency consequence | Evidence |
|---|---|---|---|
| Listener seen keys | Redis ingestion:idempotency; no expiry in mark_seen | Separate seen/publish/set permits races; unbounded retained keys; Redis loss removes replay suppression | `src/estateflow/services/telegram_listener.py:157–168`, :500–519 |
| Source refresh | Redis estateflow:listener-refresh-version + estateflow:listener-refresh pubsub | Poll catches missed pubsub only when version changes; DB write and signal not atomic | `src/estateflow/services/listener_refresh.py:46`, :69–86, :526–561 |
| Listener topology | Redis estateflow:listener-topology | Single snapshot key, timestamp but no expiry on write; stale health must be distinguished | `src/estateflow/services/listener_status.py:19`, :189–209 |
| Telegram login state | Redis with TTL; phone number and phone code hash | Sensitive ephemeral state; protect Redis independently of logs | `src/estateflow/services/telegram_auth.py:99–129` |
| Compatibility queue backend | Redis lists RPUSH/BLPOP; ACK no-op | Popped work is lost on worker crash; exhausted retries silently return; no DLQ parity | `src/estateflow/services/queue.py:79–125`; factory `src/estateflow/application/queue_factory.py:9` |
| Telethon updates | asyncio.Queue | Volatile ingress before publish; no application durable cursor/replay checkpoint found | `src/estateflow/adapters/telethon_listener.py:38`, :102–107 |
| Album assembly | dict + debounce tasks | Crash loses unflushed pieces; late pieces hit same album seen key | `src/estateflow/services/media_buffer.py:37–65`, :92–129 |
| Listener accounts/assignments/audit | In-memory repositories in coordinator | PostgreSQL pool tables do not make the active coordinator durable; no deployment-wide session fencing found | `src/estateflow/services/listener_refresh.py:124–132` |
| Pre-AI dedup | Unbounded list; disabled audit sink by default | Replica/restart changes duplicate decisions; linear candidate scan | `src/estateflow/services/pre_ai_dedup.py:104`, :127–147; `services/worker_pre_ai/main.py:25` |
| AI runs and publication flag | InMemoryAiProcessingRepository | Restart repeats LLM/media work and loses failed/manual-review audit | `services/worker_ai/main.py:47`; `src/estateflow/services/ai_worker.py:58–66` |
| Notification limiter | Disabled by default; optional in-memory limiter | No shared application rate coordination in split worker | `src/estateflow/services/notifications.py:261–285`, :367 |

## Proposed V2 contract changes (recommendations, approval pending)

Use a distinct V2 exchange name (for example estateflow.v2.events) before choosing topic routing; the existing direct exchange cannot be treated as a topic exchange in place. Separate consumer queues for search and matching, retaining V1 routes through a compatibility adapter. Freeze JSON Schemas before extraction; preserve event_id through retries and assign separate replay operation metadata. Aggregate identity must distinguish source message, album, parser candidate and canonical listing; correlation ID cannot serve all four.

Define monotonically comparable edit revisions, delete tombstones, album membership, parser policy revision/hash and replay policy. Catalog persisted/changed events must carry actual persisted canonical ID, aggregate revision, parent linkage, source identities, status, availability and media readiness. Route tombstones around AI and cheap duplicate suppression. Preserve canonical lineage when parser versions split a message differently. Specify media reference expiry and which service can redeem Telegram references; raw references alone do not remove session dependencies.

Persist ingress/album assembly before acknowledging application ingestion; owner-local inbox/outbox for each DB stage. Notification delivery needs due scheduling, atomic claims, leases and explicit ambiguous-send recovery. No Telegram exactly-once promise is supported by the current code. Quarantine requires retention, private operator access, replay auditing and a way to bypass the listener seen set safely without changing business event identity.
