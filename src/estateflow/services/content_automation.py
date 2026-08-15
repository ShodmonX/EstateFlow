from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from pydantic import SecretStr

from estateflow.services.queue import PublishingQueue, QueueMessage

CONTENT_AUTOMATION_QUEUE = "content.channel.scheduled_posts"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

ContentPostKind = Literal["daily_stats", "top_offer", "district_stats", "transparency_report"]
ContentPostStatus = Literal["drafted", "published", "dry_run", "failed"]


@dataclass(frozen=True)
class ContentStats:
    total_checked: int
    duplicates_detected: int
    active_listings: int
    average_monthly_price: Decimal | None = None


@dataclass(frozen=True)
class ContentDistrictStats:
    district: str
    listing_count: int
    average_monthly_price: Decimal
    min_monthly_price: Decimal
    max_monthly_price: Decimal


@dataclass(frozen=True)
class ContentTopOffer:
    announcement_id: str
    district: str | None
    rooms: int | None
    price_normalized_monthly: Decimal | None
    currency: str | None
    source_url: str | None = None
    area_sqm: Decimal | None = None
    source_count: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ContentPost:
    post_id: str
    kind: ContentPostKind
    idempotency_key: str
    text: str
    status: ContentPostStatus
    published_at: datetime | None = None
    publisher_message_id: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    error_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class PublishResult:
    status: ContentPostStatus
    message_id: str | None = None


class ContentPublishError(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class ContentRepository(Protocol):
    async def daily_stats(self, *, day: date) -> ContentStats: ...

    async def top_offers(self, *, day: date, limit: int) -> tuple[ContentTopOffer, ...]: ...

    async def district_stats(
        self,
        *,
        day: date,
        limit: int,
    ) -> tuple[ContentDistrictStats, ...]: ...

    async def count_posts(self, *, day: date) -> int: ...

    async def get_by_idempotency_key(self, *, idempotency_key: str) -> ContentPost | None: ...

    async def save_post(self, post: ContentPost) -> ContentPost: ...


class ContentPublisher(Protocol):
    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult: ...


class DryRunContentPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        self.messages.append((idempotency_key, text))
        return PublishResult(status="dry_run", message_id=f"dry-run:{idempotency_key}")


class TelegramContentPublisher:
    def __init__(
        self,
        *,
        bot_token: SecretStr | None,
        chat_id: str | None,
        dry_run_fallback: ContentPublisher | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._dry_run = dry_run_fallback or DryRunContentPublisher()

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        if self._bot_token is None or not self._chat_id:
            return await self._dry_run.publish(text=text, idempotency_key=idempotency_key)
        token = self._bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={
                        "chat_id": self._chat_id,
                        "text": _telegram_safe_text(text),
                        "disable_web_page_preview": True,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 500 or response.status == 429:
                        raise ContentPublishError(
                            f"telegram_retryable_http_{response.status}",
                            retryable=True,
                        )
                    if response.status >= 400 or not payload.get("ok"):
                        raise ContentPublishError(
                            f"telegram_permanent_http_{response.status}",
                            retryable=False,
                        )
                    message_id = str(payload.get("result", {}).get("message_id", ""))
                    return PublishResult(status="published", message_id=message_id or None)
        except ContentPublishError:
            raise
        except Exception as exc:
            raise ContentPublishError(type(exc).__name__, retryable=True) from exc


class InMemoryContentRepository:
    def __init__(
        self,
        *,
        stats: ContentStats | None = None,
        offers: tuple[ContentTopOffer, ...] = (),
        district_stats: tuple[ContentDistrictStats, ...] = (),
    ) -> None:
        self.stats = stats or ContentStats(
            total_checked=0,
            duplicates_detected=0,
            active_listings=0,
        )
        self.offers = offers
        self.districts = district_stats
        self.posts: dict[str, ContentPost] = {}

    async def daily_stats(self, *, day: date) -> ContentStats:
        return self.stats

    async def top_offers(self, *, day: date, limit: int) -> tuple[ContentTopOffer, ...]:
        return rank_top_offers(self.offers, limit=limit)

    async def district_stats(self, *, day: date, limit: int) -> tuple[ContentDistrictStats, ...]:
        return self.districts[:limit]

    async def count_posts(self, *, day: date) -> int:
        prefix = day.isoformat()
        return sum(
            1
            for key, post in self.posts.items()
            if key.startswith(prefix) and post.status in {"published", "dry_run", "drafted"}
        )

    async def get_by_idempotency_key(self, *, idempotency_key: str) -> ContentPost | None:
        return self.posts.get(idempotency_key)

    async def save_post(self, post: ContentPost) -> ContentPost:
        self.posts[post.idempotency_key] = post
        return post


class ContentAutomationService:
    def __init__(
        self,
        *,
        repository: ContentRepository,
        publisher: ContentPublisher | None = None,
        daily_post_limit: int = 2,
        schedule_timezone: str = "Asia/Tashkent",
        retry_backoff: timedelta = timedelta(minutes=10),
    ) -> None:
        if daily_post_limit < 1 or daily_post_limit > 2:
            raise ValueError("Content automation must be capped at 1-2 posts per day.")
        self._repository = repository
        self._publisher = publisher or DryRunContentPublisher()
        self._daily_post_limit = daily_post_limit
        self._timezone = _load_timezone(schedule_timezone)
        self._retry_backoff = retry_backoff

    async def generate_daily(self, *, now: datetime | None = None) -> tuple[ContentPost, ...]:
        current = _aware(now or datetime.now(UTC))
        day = current.astimezone(self._timezone).date()
        planned: tuple[ContentPostKind, ...] = (
            "top_offer",
            "district_stats",
            "transparency_report",
        )
        posts: list[ContentPost] = []
        for kind in planned:
            post = await self._create_once(kind=kind, day=day, now=current)
            if post is not None:
                posts.append(post)
        return tuple(posts)

    async def preview_daily(self, *, now: datetime | None = None) -> tuple[ContentPost, ...]:
        current = _aware(now or datetime.now(UTC))
        day = current.astimezone(self._timezone).date()
        planned: tuple[ContentPostKind, ...] = (
            "top_offer",
            "district_stats",
            "transparency_report",
        )
        remaining = self._daily_post_limit - await self._repository.count_posts(day=day)
        if remaining <= 0:
            return ()
        posts: list[ContentPost] = []
        for kind in planned:
            if len(posts) >= remaining:
                break
            key = f"{day.isoformat()}:{kind}"
            if await self._repository.get_by_idempotency_key(idempotency_key=key) is not None:
                continue
            text = await self._render(kind=kind, day=day)
            if text is None:
                continue
            posts.append(
                ContentPost(
                    post_id=f"preview:{key}",
                    kind=kind,
                    idempotency_key=key,
                    text=_telegram_safe_text(text),
                    status="drafted",
                    created_at=current,
                )
            )
        return tuple(posts)

    async def process_scheduled_message(
        self,
        message: QueueMessage,
        *,
        now: datetime | None = None,
    ) -> ContentPost | None:
        kind = _kind(message.payload["kind"])
        day = date.fromisoformat(str(message.payload["day"]))
        return await self._create_once(kind=kind, day=day, now=_aware(now or datetime.now(UTC)))

    async def _create_once(
        self,
        *,
        kind: ContentPostKind,
        day: date,
        now: datetime,
    ) -> ContentPost | None:
        key = f"{day.isoformat()}:{kind}"
        existing = await self._repository.get_by_idempotency_key(idempotency_key=key)
        if existing is not None:
            if existing.status in {"published", "dry_run", "drafted"}:
                return existing
            if existing.next_attempt_at is not None and existing.next_attempt_at > now:
                return existing
        if await self._repository.count_posts(day=day) >= self._daily_post_limit:
            return existing
        text = await self._render(kind=kind, day=day)
        if text is None:
            return None
        attempts = 0 if existing is None else existing.attempts
        try:
            result = await self._publisher.publish(
                text=_telegram_safe_text(text),
                idempotency_key=key,
            )
        except ContentPublishError as exc:
            failed = ContentPost(
                post_id=existing.post_id if existing is not None else str(uuid4()),
                kind=kind,
                idempotency_key=key,
                text=_telegram_safe_text(text),
                status="failed",
                attempts=attempts + 1,
                next_attempt_at=now + self._retry_backoff if exc.retryable else None,
                error_reason=exc.reason,
                created_at=existing.created_at if existing is not None else now,
            )
            return await self._repository.save_post(failed)
        return await self._repository.save_post(
            ContentPost(
                post_id=existing.post_id if existing is not None else str(uuid4()),
                kind=kind,
                idempotency_key=key,
                text=_telegram_safe_text(text),
                status=result.status,
                published_at=now if result.status in {"published", "dry_run"} else None,
                publisher_message_id=result.message_id,
                attempts=attempts + 1,
                created_at=existing.created_at if existing is not None else now,
            )
        )

    async def _render(self, *, kind: ContentPostKind, day: date) -> str | None:
        if kind in {"daily_stats", "transparency_report"}:
            stats = await self._repository.daily_stats(day=day)
            avg_text = (
                "narx yetarli emas"
                if stats.average_monthly_price is None
                else f"{stats.average_monthly_price.quantize(Decimal('1'))} USD"
            )
            return (
                "EstateFlow kunlik shaffoflik hisoboti\n"
                f"Sana: {day.isoformat()}\n"
                f"Tekshirilgan e'lonlar: {stats.total_checked}\n"
                f"Dublikatlar: {stats.duplicates_detected}\n"
                f"Faol e'lonlar: {stats.active_listings}\n"
                f"O'rtacha oylik narx: {avg_text}"
            )
        if kind == "district_stats":
            district_items = await self._repository.district_stats(day=day, limit=5)
            if not district_items:
                return None
            lines = [
                "Tumanlar bo'yicha bozor narxi statistikasi",
                f"Sana: {day.isoformat()}",
                "Faqat active canonical, umumiy oylik ijara e'lonlari hisoblandi.",
            ]
            for item in district_items:
                avg = item.average_monthly_price.quantize(Decimal("1"))
                min_price = item.min_monthly_price.quantize(Decimal("1"))
                max_price = item.max_monthly_price.quantize(Decimal("1"))
                lines.append(
                    f"{item.district}: o'rtacha {avg} USD "
                    f"({item.listing_count} ta, min {min_price}, max {max_price})"
                )
            return "\n".join(lines)
        offers = await self._repository.top_offers(day=day, limit=1)
        if not offers:
            return None
        offer = offers[0]
        price = (
            "narx ko'rsatilmagan"
            if offer.price_normalized_monthly is None
            else (
                f"{offer.price_normalized_monthly.quantize(Decimal('1'))} {offer.currency or ''}"
            ).strip()
        )
        lines = [
            "Bugungi konservativ top taklif",
            f"Tuman: {offer.district or 'nomalum'}",
            f"Xona: {offer.rooms if offer.rooms is not None else 'nomalum'}",
            f"Oylik narx: {price}",
            "Ranking sababi: umumiy oylik narxi aniq, active canonical e'lonlar "
            "ichida arzon kandidat.",
            "Eslatma: bu kafolat yoki tavsiya emas; ma'lumotni manbadan tekshiring.",
        ]
        if offer.source_url:
            lines.append(f"Manba: {_safe_source_url(offer.source_url)}")
        return "\n".join(lines)


class ContentScheduleService:
    def __init__(
        self,
        *,
        queue: PublishingQueue,
        schedule_timezone: str = "Asia/Tashkent",
    ) -> None:
        self._queue = queue
        self._timezone = _load_timezone(schedule_timezone)

    async def enqueue_daily(
        self,
        *,
        now: datetime | None = None,
        kinds: tuple[ContentPostKind, ...] = (
            "top_offer",
            "district_stats",
            "transparency_report",
        ),
    ) -> tuple[QueueMessage, ...]:
        current = _aware(now or datetime.now(UTC))
        day = current.astimezone(self._timezone).date()
        messages: list[QueueMessage] = []
        for kind in kinds:
            key = f"{day.isoformat()}:{kind}"
            message = QueueMessage(
                queue_name=CONTENT_AUTOMATION_QUEUE,
                payload={
                    "schema_version": "estateflow.content.schedule.v1",
                    "kind": kind,
                    "day": day.isoformat(),
                    "idempotency_key": key,
                    "timezone": str(self._timezone),
                },
                correlation_id=f"content:{key}",
                metadata={"idempotency_key": key, "kind": kind},
            )
            await self._queue.publish(message)
            messages.append(message)
        return tuple(messages)


def rank_top_offers(
    offers: tuple[ContentTopOffer, ...],
    *,
    limit: int,
) -> tuple[ContentTopOffer, ...]:
    reliable = [
        offer
        for offer in offers
        if offer.price_normalized_monthly is not None
        and offer.price_normalized_monthly > 0
        and offer.currency in {"USD", "UZS"}
    ]
    reliable.sort(
        key=lambda offer: (
            offer.price_normalized_monthly or Decimal("999999999"),
            -(offer.source_count),
            offer.created_at,
            offer.announcement_id,
        )
    )
    return tuple(reliable[:limit])


def _telegram_safe_text(value: str) -> str:
    normalized = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if len(normalized) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return normalized
    return normalized[: TELEGRAM_MAX_MESSAGE_LENGTH - 3].rstrip() + "..."


def _safe_source_url(value: str) -> str:
    if value.startswith(("https://t.me/", "https://telegram.me/")):
        return value
    return "manba havolasi admin scope'da mavjud"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _load_timezone(value: str) -> tzinfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        if value == "Asia/Tashkent":
            return timezone(timedelta(hours=5), value)
        raise


def _kind(value: Any) -> ContentPostKind:
    if value not in {"daily_stats", "top_offer", "district_stats", "transparency_report"}:
        raise ValueError(f"Unsupported content kind: {value}")
    return cast(ContentPostKind, value)
