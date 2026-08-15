from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, cast
from uuid import uuid4

from estateflow.application.core.redaction import redact_text
from estateflow.services.analytics import (
    AnalyticsRecorder,
    TechnicalMetricName,
    TechnicalMetricRecorder,
    safe_record_event,
    safe_record_technical_metric,
)
from estateflow.services.ops_notifications import (
    DisabledOpsNotificationService,
    OpsNotificationService,
)
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.saved_filters import UserFilter
from estateflow.services.search import announcement_matches

NotificationStatus = Literal["pending", "sending", "sent", "failed", "suppressed"]
NotificationPriority = Literal["standard", "high"]
NotificationQueueName = Literal["notifications.standard", "notifications.high_priority"]
NotificationAttemptResult = Literal[
    "sending",
    "sent",
    "retryable_failure",
    "permanent_failure",
    "rate_limited",
    "skipped",
]
NotificationAction = Literal["open", "search", "read"]

STANDARD_NOTIFICATION_PRIORITY = 0
PREMIUM_NOTIFICATION_PRIORITY = 10
STANDARD_NOTIFICATION_QUEUE: NotificationQueueName = "notifications.standard"
HIGH_PRIORITY_NOTIFICATION_QUEUE: NotificationQueueName = "notifications.high_priority"


@dataclass(frozen=True)
class NotificationUser:
    user_id: int
    notifications_enabled: bool = True
    premium_until: datetime | None = None
    status: str = "active"

    def is_premium(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.premium_until is not None and self.premium_until > current


@dataclass(frozen=True)
class NotificationJob:
    notification_id: str
    user_id: int
    filter_id: str
    announcement_id: str
    status: NotificationStatus = "pending"
    priority: int = STANDARD_NOTIFICATION_PRIORITY
    queue_name: NotificationQueueName = STANDARD_NOTIFICATION_QUEUE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempts: int = 0
    next_attempt_at: datetime | None = None
    telegram_message_id: str | None = None

    @property
    def idempotency_key(self) -> str:
        return notification_idempotency_key(
            user_id=self.user_id,
            filter_id=self.filter_id,
            announcement_id=self.announcement_id,
        )


@dataclass(frozen=True)
class NotificationMatchMetrics:
    matched_filters: int = 0
    jobs_created: int = 0
    duplicate_jobs: int = 0
    skipped_disabled_filters: int = 0
    skipped_non_canonical: int = 0
    skipped_inactive_user: int = 0
    skipped_notifications_disabled: int = 0
    skipped_no_match: int = 0
    matching_errors: int = 0

    def add(self, **updates: int) -> NotificationMatchMetrics:
        data = self.__dict__ | updates
        return NotificationMatchMetrics(**data)


@dataclass(frozen=True)
class NotificationMatchResult:
    jobs: tuple[NotificationJob, ...]
    metrics: NotificationMatchMetrics
    skipped_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotificationDeliveryContext:
    job: NotificationJob
    announcement: StructuredAnnouncement
    filter_name: str


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: str


@dataclass(frozen=True)
class NotificationDeliveryAttempt:
    notification_id: str
    attempt_no: int
    result: NotificationAttemptResult
    error_type: str | None = None
    retryable: bool = False
    telegram_message_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class NotificationDeliveryMetrics:
    picked: int = 0
    sent: int = 0
    retryable_failures: int = 0
    permanent_failures: int = 0
    rate_limited: int = 0
    skipped: int = 0

    def add(self, **updates: int) -> NotificationDeliveryMetrics:
        data = self.__dict__ | updates
        return NotificationDeliveryMetrics(**data)


@dataclass(frozen=True)
class NotificationDeliveryBatchResult:
    metrics: NotificationDeliveryMetrics
    attempts: tuple[NotificationDeliveryAttempt, ...]


class TelegramDeliveryError(Exception):
    retryable = True


class TelegramRateLimitError(TelegramDeliveryError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("telegram_rate_limited")
        self.retry_after_seconds = retry_after_seconds


class TelegramUserBlockedError(TelegramDeliveryError):
    retryable = False


class TelegramTransientError(TelegramDeliveryError):
    retryable = True


class TelegramPermanentError(TelegramDeliveryError):
    retryable = False


class NotificationSavedFilterRepository(Protocol):
    async def list_for_matching(self) -> list[UserFilter]: ...


class NotificationUserRepository(Protocol):
    async def get_notification_user(self, *, user_id: int) -> NotificationUser | None: ...


class NotificationJobRepository(Protocol):
    async def create_pending(
        self,
        *,
        user_id: int,
        filter_id: str,
        announcement_id: str,
        priority: int,
        queue_name: NotificationQueueName,
    ) -> tuple[NotificationJob, bool]: ...


class NotificationDeliveryRepository(Protocol):
    async def list_ready(self, *, limit: int, now: datetime) -> list[NotificationJob]: ...

    async def get_delivery_context(
        self,
        *,
        notification_id: str,
    ) -> NotificationDeliveryContext | None: ...

    async def mark_sending(
        self,
        *,
        notification_id: str,
        now: datetime,
    ) -> NotificationJob | None: ...

    async def mark_sent(
        self,
        *,
        notification_id: str,
        telegram_message_id: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt: ...

    async def mark_retryable_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        retry_after: timedelta,
        now: datetime,
    ) -> NotificationDeliveryAttempt: ...

    async def mark_permanent_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt: ...


class TelegramNotificationClient(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult: ...


class NotificationRateLimiter(Protocol):
    async def retry_after(self, *, user_id: int, now: datetime) -> timedelta | None: ...


class DisabledNotificationRateLimiter:
    async def retry_after(self, *, user_id: int, now: datetime) -> timedelta | None:
        return None


class NotificationQueuePublisher(Protocol):
    async def publish(self, job: NotificationJob) -> None: ...


class InMemoryNotificationQueuePublisher:
    def __init__(self) -> None:
        self.jobs: list[NotificationJob] = []

    async def publish(self, job: NotificationJob) -> None:
        self.jobs.append(job)


class InMemoryNotificationRateLimiter:
    def __init__(
        self,
        *,
        per_user_interval: timedelta = timedelta(),
        global_interval: timedelta = timedelta(),
    ) -> None:
        self._per_user_interval = per_user_interval
        self._global_interval = global_interval
        self._last_user_send: dict[int, datetime] = {}
        self._last_global_send: datetime | None = None

    async def retry_after(self, *, user_id: int, now: datetime) -> timedelta | None:
        retry_after = _remaining(self._last_global_send, self._global_interval, now)
        user_retry_after = _remaining(
            self._last_user_send.get(user_id),
            self._per_user_interval,
            now,
        )
        if retry_after is None or (user_retry_after is not None and user_retry_after > retry_after):
            retry_after = user_retry_after
        if retry_after is None:
            self._last_global_send = now
            self._last_user_send[user_id] = now
        return retry_after


class NotificationMessageFormatter:
    def format_message(self, context: NotificationDeliveryContext) -> str:
        listing = context.announcement.canonical
        price_phrase = _format_price_phrase(
            listing.price,
            listing.currency,
            listing.price_period,
            listing.price_basis,
        )
        lines = [
            "Siz saqlagan filtringizga mos yangi e'lon topildi.",
            "",
            f"Filtr: {context.filter_name}",
            f"Narxi: {price_phrase}",
        ]
        if listing.district:
            lines.append(f"Joylashuvi: {listing.district}")
        if listing.rooms is not None:
            lines.append(f"Xonalar: {_format_rooms(listing.rooms)}")
        if listing.area_sqm is not None:
            lines.append(f"Maydoni: {_format_number(listing.area_sqm)} m2")
        if listing.renovation_level:
            lines.append(f"Ta'miri: {_renovation_label(listing.renovation_level)}")
        if listing.furniture is not None:
            lines.append(f"Mebel: {_furniture_label(listing.furniture)}")
        if listing.address:
            lines.append(f"Manzil: {listing.address}")
        description = _shorten(redact_text(listing.description.strip()), limit=220)
        if description:
            lines.extend(["", f"Qisqacha: {description}"])
        if context.announcement.source_count > 1:
            lines.append(
                f"Bu e'lon {context.announcement.source_count} ta kanalda uchradi."
            )
        if context.announcement.source_url:
            lines.append(f"Manba: {context.announcement.source_url}")
        return "\n".join(lines)

    def format_actions(self, context: NotificationDeliveryContext) -> dict[str, object]:
        buttons = [
            [
                {
                    "text": "E'lonni ochish",
                    "callback_data": notification_action_callback(
                        notification_id=context.job.notification_id,
                        action="open",
                    ),
                }
            ],
            [
                {
                    "text": "Shu filtrda qidirish",
                    "callback_data": notification_action_callback(
                        notification_id=context.job.notification_id,
                        action="search",
                    ),
                }
            ],
        ]
        return {"inline_keyboard": buttons}


class NotificationDeliveryWorker:
    def __init__(
        self,
        *,
        repository: NotificationDeliveryRepository,
        telegram: TelegramNotificationClient,
        formatter: NotificationMessageFormatter | None = None,
        rate_limiter: NotificationRateLimiter | None = None,
        ops_notifier: OpsNotificationService | None = None,
        analytics_recorder: AnalyticsRecorder | None = None,
        technical_recorder: TechnicalMetricRecorder | None = None,
        bulk_failure_threshold: int = 5,
        retry_backoff: timedelta = timedelta(minutes=5),
    ) -> None:
        self._repository = repository
        self._telegram = telegram
        self._formatter = formatter or NotificationMessageFormatter()
        self._rate_limiter = rate_limiter or DisabledNotificationRateLimiter()
        self._ops_notifier = ops_notifier or DisabledOpsNotificationService()
        self._analytics_recorder = analytics_recorder
        self._technical_recorder = technical_recorder
        self._bulk_failure_threshold = bulk_failure_threshold
        self._retry_backoff = retry_backoff

    async def process_batch(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> NotificationDeliveryBatchResult:
        current = now or datetime.now(UTC)
        jobs = await self._repository.list_ready(limit=limit, now=current)
        metrics = NotificationDeliveryMetrics(picked=len(jobs))
        attempts: list[NotificationDeliveryAttempt] = []
        bulk_failures = 0
        for job in jobs:
            attempt, metric_updates = await self._process_job(job, now=current)
            attempts.append(attempt)
            metrics = metrics.add(
                **{key: getattr(metrics, key) + value for key, value in metric_updates.items()}
            )
            if attempt.result in {"retryable_failure", "permanent_failure"}:
                bulk_failures += 1
        if bulk_failures >= self._bulk_failure_threshold:
            await self._ops_notifier.notify(
                severity="warning",
                reason=f"notification delivery bulk failure count={bulk_failures}",
            )
        return NotificationDeliveryBatchResult(metrics=metrics, attempts=tuple(attempts))

    async def process_job(
        self,
        job: NotificationJob,
        *,
        now: datetime | None = None,
    ) -> NotificationDeliveryAttempt:
        """Process one RabbitMQ-delivered notification job."""
        attempt, _metrics = await self._process_job(job, now=now or datetime.now(UTC))
        return attempt

    async def _process_job(
        self,
        job: NotificationJob,
        *,
        now: datetime,
    ) -> tuple[NotificationDeliveryAttempt, dict[str, int]]:
        sending = await self._repository.mark_sending(notification_id=job.notification_id, now=now)
        if sending is None:
            return (
                NotificationDeliveryAttempt(
                    notification_id=job.notification_id,
                    attempt_no=job.attempts,
                    result="skipped",
                ),
                {"skipped": 1},
            )
        retry_after = await self._rate_limiter.retry_after(user_id=sending.user_id, now=now)
        if retry_after is not None:
            attempt = await self._repository.mark_retryable_failure(
                notification_id=sending.notification_id,
                error_type="local_rate_limited",
                retry_after=retry_after,
                now=now,
            )
            await self._record_technical_delivery_failure(sending, attempt)
            return attempt, {"rate_limited": 1}

        context = await self._repository.get_delivery_context(
            notification_id=sending.notification_id
        )
        if context is None:
            attempt = await self._repository.mark_permanent_failure(
                notification_id=sending.notification_id,
                error_type="missing_delivery_context",
                now=now,
            )
            await self._record_technical_delivery_failure(sending, attempt)
            return attempt, {"permanent_failures": 1}

        try:
            result = await self._telegram.send_message(
                chat_id=sending.user_id,
                text=self._formatter.format_message(context),
                reply_markup=self._formatter.format_actions(context),
            )
        except TelegramRateLimitError as exc:
            attempt = await self._repository.mark_retryable_failure(
                notification_id=sending.notification_id,
                error_type="telegram_rate_limited",
                retry_after=timedelta(seconds=exc.retry_after_seconds),
                now=now,
            )
            await self._record_delivery_failure(sending, attempt)
            return attempt, {"retryable_failures": 1}
        except TelegramUserBlockedError:
            attempt = await self._repository.mark_permanent_failure(
                notification_id=sending.notification_id,
                error_type="telegram_user_blocked",
                now=now,
            )
            await self._record_delivery_failure(sending, attempt)
            return attempt, {"permanent_failures": 1}
        except TelegramPermanentError:
            attempt = await self._repository.mark_permanent_failure(
                notification_id=sending.notification_id,
                error_type="telegram_permanent_error",
                now=now,
            )
            await self._record_delivery_failure(sending, attempt)
            return attempt, {"permanent_failures": 1}
        except TelegramTransientError:
            attempt = await self._repository.mark_retryable_failure(
                notification_id=sending.notification_id,
                error_type="telegram_transient_error",
                retry_after=self._retry_backoff,
                now=now,
            )
            await self._record_delivery_failure(sending, attempt)
            return attempt, {"retryable_failures": 1}

        attempt = await self._repository.mark_sent(
            notification_id=sending.notification_id,
            telegram_message_id=result.message_id,
            now=now,
        )
        await safe_record_event(
            self._analytics_recorder,
            event_name="notification_sent",
            idempotency_key=f"notification_sent:{sending.notification_id}",
            occurred_at=attempt.created_at,
            user_id=sending.user_id,
            subject_id=sending.notification_id,
            metadata={"telegram_message_id": result.message_id},
        )
        return attempt, {"sent": 1}

    async def _record_delivery_failure(
        self,
        job: NotificationJob,
        attempt: NotificationDeliveryAttempt,
    ) -> None:
        await self._record_technical_delivery_failure(job, attempt)
        await safe_record_event(
            self._analytics_recorder,
            event_name="notification_delivery_failed",
            idempotency_key=f"notification_delivery_failed:{job.notification_id}:{attempt.attempt_no}",
            occurred_at=attempt.created_at,
            user_id=job.user_id,
            subject_id=job.notification_id,
            metadata={"error_type": attempt.error_type or "unknown"},
        )

    async def _record_technical_delivery_failure(
        self,
        job: NotificationJob,
        attempt: NotificationDeliveryAttempt,
    ) -> None:
        metric_name: TechnicalMetricName = (
            "notification_retry"
            if attempt.result in {"retryable_failure", "rate_limited"}
            else "notification_error"
        )
        await safe_record_technical_metric(
            self._technical_recorder,
            metric_name=metric_name,
            idempotency_key=(
                f"{metric_name}:{job.notification_id}:{attempt.attempt_no}:"
                f"{attempt.error_type or 'unknown'}"
            ),
            occurred_at=attempt.created_at,
            component="notification_delivery",
            subject_id=job.notification_id,
            metadata={
                "result": attempt.result,
                "error_type": attempt.error_type or "unknown",
                "queue_name": job.queue_name,
            },
        )


class NotificationMatchingEngine:
    def __init__(
        self,
        *,
        filter_repository: NotificationSavedFilterRepository,
        user_repository: NotificationUserRepository,
        job_repository: NotificationJobRepository,
        queue_publisher: NotificationQueuePublisher,
        ops_notifier: OpsNotificationService | None = None,
    ) -> None:
        self._filter_repository = filter_repository
        self._user_repository = user_repository
        self._job_repository = job_repository
        self._queue_publisher = queue_publisher
        self._ops_notifier = ops_notifier or DisabledOpsNotificationService()

    async def match_announcement(
        self,
        announcement: StructuredAnnouncement,
        now: datetime | None = None,
    ) -> NotificationMatchResult:
        metrics = NotificationMatchMetrics()
        skipped: list[str] = []
        if announcement.status != "active" or announcement.parent_id is not None:
            reason = "announcement_not_active_canonical_parent"
            return NotificationMatchResult(
                jobs=(),
                metrics=metrics.add(skipped_non_canonical=1),
                skipped_reasons=(reason,),
            )

        jobs: list[NotificationJob] = []
        filters = await self._filter_repository.list_for_matching()
        for saved_filter in filters:
            if not saved_filter.enabled:
                metrics = metrics.add(skipped_disabled_filters=metrics.skipped_disabled_filters + 1)
                continue
            try:
                if not announcement_matches(announcement, saved_filter.criteria):
                    metrics = metrics.add(skipped_no_match=metrics.skipped_no_match + 1)
                    continue
            except Exception as exc:
                metrics = metrics.add(matching_errors=metrics.matching_errors + 1)
                skipped.append(f"filter_match_error:{saved_filter.filter_id}")
                await self._ops_notifier.notify(
                    severity="warning",
                    reason=f"notification filter match failed: {type(exc).__name__}",
                )
                continue

            user = await self._user_repository.get_notification_user(user_id=saved_filter.user_id)
            if user is None or user.status != "active":
                metrics = metrics.add(skipped_inactive_user=metrics.skipped_inactive_user + 1)
                continue
            if not user.notifications_enabled:
                metrics = metrics.add(
                    skipped_notifications_disabled=metrics.skipped_notifications_disabled + 1
                )
                continue

            queue_name, priority = _priority_for_user(user, now=now)
            job, created = await self._job_repository.create_pending(
                user_id=saved_filter.user_id,
                filter_id=saved_filter.filter_id,
                announcement_id=announcement.announcement_id,
                priority=priority,
                queue_name=queue_name,
            )
            if created:
                await self._queue_publisher.publish(job)
                jobs.append(job)
                metrics = metrics.add(
                    matched_filters=metrics.matched_filters + 1,
                    jobs_created=metrics.jobs_created + 1,
                )
            else:
                metrics = metrics.add(
                    matched_filters=metrics.matched_filters + 1,
                    duplicate_jobs=metrics.duplicate_jobs + 1,
                )
        return NotificationMatchResult(
            jobs=tuple(jobs),
            metrics=metrics,
            skipped_reasons=tuple(skipped),
        )


class InMemoryNotificationJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, NotificationJob] = {}
        self.announcements: dict[str, StructuredAnnouncement] = {}
        self.filter_names: dict[str, str] = {}
        self.attempts: list[NotificationDeliveryAttempt] = []

    async def create_pending(
        self,
        *,
        user_id: int,
        filter_id: str,
        announcement_id: str,
        priority: int,
        queue_name: NotificationQueueName,
    ) -> tuple[NotificationJob, bool]:
        key = notification_idempotency_key(
            user_id=user_id,
            filter_id=filter_id,
            announcement_id=announcement_id,
        )
        existing = self.jobs.get(key)
        if existing is not None:
            return existing, False
        job = NotificationJob(
            notification_id=str(uuid4()),
            user_id=user_id,
            filter_id=filter_id,
            announcement_id=announcement_id,
            priority=priority,
            queue_name=queue_name,
        )
        self.jobs[key] = job
        return job, True

    def add_delivery_context(
        self,
        *,
        announcement: StructuredAnnouncement,
        saved_filter: UserFilter,
    ) -> None:
        self.announcements[announcement.announcement_id] = announcement
        self.filter_names[saved_filter.filter_id] = saved_filter.name

    async def list_ready(self, *, limit: int, now: datetime) -> list[NotificationJob]:
        ready = [
            job
            for job in self.jobs.values()
            if job.status == "pending"
            and (job.next_attempt_at is None or job.next_attempt_at <= now)
        ]
        ready.sort(key=lambda item: (-item.priority, item.created_at, item.notification_id))
        return ready[:limit]

    async def get_delivery_context(
        self,
        *,
        notification_id: str,
    ) -> NotificationDeliveryContext | None:
        job = self._get_by_notification_id(notification_id)
        if job is None:
            return None
        announcement = self.announcements.get(job.announcement_id)
        filter_name = self.filter_names.get(job.filter_id)
        if announcement is None or filter_name is None:
            return None
        return NotificationDeliveryContext(
            job=job,
            announcement=announcement,
            filter_name=filter_name,
        )

    async def mark_sending(
        self,
        *,
        notification_id: str,
        now: datetime,
    ) -> NotificationJob | None:
        job = self._get_by_notification_id(notification_id)
        if job is None or job.status != "pending":
            return None
        updated = replace(job, status="sending", attempts=job.attempts + 1)
        self.jobs[updated.idempotency_key] = updated
        self.attempts.append(
            NotificationDeliveryAttempt(
                notification_id=notification_id,
                attempt_no=updated.attempts,
                result="sending",
                created_at=now,
            )
        )
        return updated

    async def mark_sent(
        self,
        *,
        notification_id: str,
        telegram_message_id: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        job = self._require_by_notification_id(notification_id)
        updated = replace(
            job,
            status="sent",
            telegram_message_id=telegram_message_id,
            next_attempt_at=None,
        )
        self.jobs[updated.idempotency_key] = updated
        return self._record_attempt(
            notification_id=notification_id,
            attempt_no=updated.attempts,
            result="sent",
            telegram_message_id=telegram_message_id,
            now=now,
        )

    async def mark_retryable_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        retry_after: timedelta,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        job = self._require_by_notification_id(notification_id)
        updated = replace(job, status="pending", next_attempt_at=now + retry_after)
        self.jobs[updated.idempotency_key] = updated
        result: NotificationAttemptResult = (
            "rate_limited" if "rate_limited" in error_type else "retryable_failure"
        )
        return self._record_attempt(
            notification_id=notification_id,
            attempt_no=updated.attempts,
            result=result,
            error_type=error_type,
            retryable=True,
            now=now,
        )

    async def mark_permanent_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        job = self._require_by_notification_id(notification_id)
        status: NotificationStatus = (
            "suppressed" if error_type == "telegram_user_blocked" else "failed"
        )
        updated = replace(job, status=status, next_attempt_at=None)
        self.jobs[updated.idempotency_key] = updated
        return self._record_attempt(
            notification_id=notification_id,
            attempt_no=updated.attempts,
            result="permanent_failure",
            error_type=error_type,
            retryable=False,
            now=now,
        )

    def _record_attempt(
        self,
        *,
        notification_id: str,
        attempt_no: int,
        result: NotificationAttemptResult,
        error_type: str | None = None,
        retryable: bool = False,
        telegram_message_id: str | None = None,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        attempt = NotificationDeliveryAttempt(
            notification_id=notification_id,
            attempt_no=attempt_no,
            result=result,
            error_type=error_type,
            retryable=retryable,
            telegram_message_id=telegram_message_id,
            created_at=now,
        )
        self.attempts.append(attempt)
        return attempt

    def _get_by_notification_id(self, notification_id: str) -> NotificationJob | None:
        return next(
            (job for job in self.jobs.values() if job.notification_id == notification_id),
            None,
        )

    def _require_by_notification_id(self, notification_id: str) -> NotificationJob:
        job = self._get_by_notification_id(notification_id)
        if job is None:
            raise KeyError(notification_id)
        return job


def notification_idempotency_key(*, user_id: int, filter_id: str, announcement_id: str) -> str:
    return f"notification:{user_id}:{filter_id}:{announcement_id}"


def notification_action_callback(*, notification_id: str, action: NotificationAction) -> str:
    if action not in {"open", "search", "read"}:
        raise ValueError("Unsupported notification action.")
    return f"n:v1:{action}:{notification_id}"


def parse_notification_action_callback(value: str) -> tuple[NotificationAction, str]:
    parts = value.split(":", 3)
    if len(parts) != 4 or parts[0] != "n" or parts[1] != "v1":
        raise ValueError("Unsupported notification callback.")
    action = parts[2]
    if action not in {"open", "search", "read"}:
        raise ValueError("Unsupported notification action.")
    return cast(NotificationAction, action), parts[3]


def _priority_for_user(
    user: NotificationUser, now: datetime | None = None
) -> tuple[NotificationQueueName, int]:
    if user.is_premium(now=now):
        return HIGH_PRIORITY_NOTIFICATION_QUEUE, PREMIUM_NOTIFICATION_PRIORITY
    return STANDARD_NOTIFICATION_QUEUE, STANDARD_NOTIFICATION_PRIORITY


def _remaining(
    last_seen: datetime | None,
    interval: timedelta,
    now: datetime,
) -> timedelta | None:
    if last_seen is None or interval <= timedelta():
        return None
    elapsed = now - last_seen
    if elapsed >= interval:
        return None
    return interval - elapsed


def _format_price(price: Decimal | None, currency: str | None) -> str:
    if price is None:
        return "narx ko'rsatilmagan"
    amount = price.quantize(Decimal("1")) if price == price.to_integral() else price
    return f"{amount} {currency or ''}".strip()


def _format_price_phrase(
    price: Decimal | None,
    currency: str | None,
    period: str,
    basis: str,
) -> str:
    if price is None:
        return "ko'rsatilmagan"
    amount = _format_price(price, currency)
    if period == "daily":
        phrase = f"kuniga {amount}"
    elif period == "one_time":
        phrase = f"{amount} (sotuv uchun)"
    else:
        phrase = f"oyiga {amount}"
    if basis == "per_person":
        phrase += ", bir kishi uchun"
    return phrase


def _format_rooms(rooms: int | None) -> str:
    if rooms is None:
        return "ko'rsatilmagan"
    return f"{rooms} ta"


def _format_number(value: Decimal) -> str:
    return f"{value:g}"


def _renovation_label(value: str) -> str:
    return {
        "none": "ta'mirsiz",
        "basic": "oddiy ta'mir",
        "good": "yaxshi ta'mir",
        "euro": "euro ta'mir",
        "luxury": "premium ta'mir",
    }.get(value, value)


def _furniture_label(value: bool) -> str:
    return "bor" if value else "yo'q"


def _shorten(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."
