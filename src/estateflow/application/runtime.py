# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from aiogram import Bot
from aiogram.exceptions import (
    RestartingTelegram,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardMarkup
from redis.asyncio import Redis

from estateflow.adapters.registry import AdapterRegistry
from estateflow.application.core.config import Settings
from estateflow.bot.controller import BotController
from estateflow.db.session import DatabaseSessionManager, create_session_manager
from estateflow.services.queue import EventQueue
from estateflow.application.queue_factory import create_event_queue
from estateflow.services.listener_refresh import (
    RedisListenerRefreshStore,
    TelegramListenerCoordinator,
)
from estateflow.repositories.release_controls import SQLAlchemyFeatureFlagStore
from estateflow.repositories.sqlalchemy import (
    SQLAlchemyAnalyticsEventRepository,
    SQLAlchemyAudienceTagRepository,
    SQLAlchemyContentRepository,
    SQLAlchemyNotificationDeliveryRepository,
    SQLAlchemyNotificationJobRepository,
    SQLAlchemyNotificationSavedFilterRepository,
    SQLAlchemyNotificationUserRepository,
    SQLAlchemySourceConfigRepository,
    SQLAlchemySavedFilterRepository,
    SQLAlchemySearchRepository,
    SQLAlchemySourceSuggestionRepository,
    SQLAlchemyTechnicalMetricRepository,
    SQLAlchemyUserRepository,
)
from estateflow.services.analytics import (
    AnalyticsRecorder,
    ProductMetricsService,
    TechnicalMetricRecorder,
)
from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
from estateflow.services.ai_client import create_openrouter_llm_client
from estateflow.services.audience_tags import AudienceTagService
from estateflow.services.content_automation import (
    ContentAutomationService,
    ContentPublisher,
    DryRunContentPublisher,
    TelegramContentPublisher,
)
from estateflow.services.health import InfrastructureHealthChecker
from estateflow.services.listener_status import ListenerTopologyService, RedisListenerTopologyStore
from estateflow.services.notifications import (
    NotificationDeliveryRepository,
    NotificationDeliveryWorker,
    NotificationMatchingEngine,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramSendResult,
    TelegramTransientError,
    TelegramUserBlockedError,
)
from estateflow.services.nlp_search import NlpSearchExtractor
from estateflow.services.referrals import ReferralService
from estateflow.services.ops_notifications import (
    DisabledOpsNotificationService,
    OpsNotificationService,
    TelegramOpsNotificationService,
)
from estateflow.repositories.post_ai_dedup import (
    LazyAsyncpgParentChildAnnouncementRepository,
)
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewQueueService,
    PostAiDedupProcessor,
    WeightedDeduplicationEngine,
    dedup_scoring_config_from_settings,
)

from estateflow.services.release_controls import (
    ReleaseControlService,
    FeatureGatedSourceProvider,
    beta_policy_from_settings,
    feature_flags_from_settings,
)
from estateflow.services.telegram_auth import RedisTelegramAuthStore, TelegramAuthService
from estateflow.services.telegram_sessions import TelegramSessionInventoryService
from estateflow.services.saved_filters import SavedFilterService
from estateflow.services.search import SearchService
from estateflow.services.source_suggestions import SourceSuggestionService
from estateflow.services.users import UserService
from estateflow.services.website_scraper import PluginWebsiteAdapterRegistry, WebsiteScraperService

from estateflow.services.queue_consumer import (
    IngestionQueueConsumerWorker,
    QueueStage,
    create_queue_consumer_worker,
)

Role = Literal["api", "bot", "worker"]


@dataclass
class EstateFlowRuntime:
    settings: Settings
    db: DatabaseSessionManager
    redis: Redis
    adapter_registry: AdapterRegistry
    listener_refresh_store: RedisListenerRefreshStore
    listener_coordinator: TelegramListenerCoordinator
    queue_consumer: IngestionQueueConsumerWorker
    health_checker: InfrastructureHealthChecker
    release_controls: ReleaseControlService
    ops_notifier: OpsNotificationService
    analytics_recorder: AnalyticsRecorder | None
    technical_recorder: TechnicalMetricRecorder | None
    source_registry: SQLAlchemySourceConfigRepository
    listener_topology_service: ListenerTopologyService
    telegram_session_inventory_service: TelegramSessionInventoryService
    website_scraper_service: WebsiteScraperService
    search_service: SearchService
    saved_filter_service: SavedFilterService
    source_suggestion_service: SourceSuggestionService
    telegram_auth_service: TelegramAuthService
    audience_tag_service: AudienceTagService
    content_automation_service: ContentAutomationService
    metrics_service: ProductMetricsService
    user_service: UserService
    manual_review_queue: ManualReviewQueueService
    post_ai_dedup_processor: PostAiDedupProcessor
    notification_matching_engine: NotificationMatchingEngine
    notification_delivery_repository: NotificationDeliveryRepository | None
    notification_bot: Bot | None
    bot_controller: BotController
    referral_service: ReferralService | None = None
    event_queue: EventQueue | None = None
    nlp_search_extractor: NlpSearchExtractor | None = None

    async def aclose(self) -> None:
        self.queue_consumer.stop()
        await self.listener_coordinator.aclose()
        if self.notification_bot is not None:
            await self.notification_bot.close()
        if self.event_queue is not None:
            await self.event_queue.aclose()
        await self.redis.aclose()
        await self.db.dispose()


def build_runtime(settings: Settings, *, role: Role = "api") -> EstateFlowRuntime:
    db = create_session_manager(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    event_queue = create_event_queue(settings, redis)
    adapter_registry = AdapterRegistry(
        _adapter_plugin_dir(settings),
        settings,
    )
    adapter_registry.reload_sync()
    health_checker = InfrastructureHealthChecker(
        db_session_manager=db,
        redis=redis,
        event_queue=event_queue,
    )
    listener_refresh_store = RedisListenerRefreshStore(redis)
    listener_topology_store = RedisListenerTopologyStore(redis)

    analytics_repo = SQLAlchemyAnalyticsEventRepository(db.session)
    technical_repo = SQLAlchemyTechnicalMetricRepository(db.session)
    search_repo = SQLAlchemySearchRepository(db.session)
    saved_filter_repo = SQLAlchemySavedFilterRepository(db.session)
    source_suggestion_repo = SQLAlchemySourceSuggestionRepository(db.session)
    audience_tag_repo = SQLAlchemyAudienceTagRepository(db.session)
    content_repo = SQLAlchemyContentRepository(db.session)
    user_repo = SQLAlchemyUserRepository(db.session)
    source_registry = SQLAlchemySourceConfigRepository(db.session)

    analytics_recorder = AnalyticsRecorder(analytics_repo)
    technical_recorder = TechnicalMetricRecorder(technical_repo)
    metrics_service = ProductMetricsService(
        analytics_repo,
        technical_repository=technical_repo,
    )

    release_controls = ReleaseControlService(
        store=SQLAlchemyFeatureFlagStore(
            db.session,
            initial=feature_flags_from_settings(settings),
        ),
        cohort_policy=beta_policy_from_settings(settings),
    )

    ops_notifier = _ops_notifier(settings)
    nlp_search_extractor = _build_nlp_search_extractor(settings, ops_notifier)
    search_service = SearchService(search_repo)
    user_service = UserService(user_repo, analytics_recorder=analytics_recorder)
    referral_service = ReferralService(
        repository=user_repo,
        analytics_recorder=analytics_recorder,
    )
    saved_filter_service = SavedFilterService(
        saved_filter_repo,
        activation_recorder=referral_service,
        analytics_recorder=analytics_recorder,
    )
    website_scraper_service = WebsiteScraperService(
        source_provider=source_registry,
        adapter_registry=PluginWebsiteAdapterRegistry(adapter_registry),
    )
    listener_coordinator = TelegramListenerCoordinator(
        settings=settings,
        adapter_registry=adapter_registry,
        source_provider=FeatureGatedSourceProvider(
            source_registry,
            release_controls=release_controls,
        ),
        refresh_store=listener_refresh_store,
        redis=redis,
        ops_notifier=ops_notifier,
        event_queue=event_queue,
        topology_store=listener_topology_store,
        listener_account_key=settings.default_telegram_listener_account_key,
        default_adapter_name=DEFAULT_TELEGRAM_ADAPTER_NAME,
    )
    source_suggestion_service = SourceSuggestionService(
        source_suggestion_repo,
        admin_notifier=None,
    )
    telegram_auth_service = TelegramAuthService(
        settings,
        store=RedisTelegramAuthStore(redis),
    )
    telegram_session_inventory_service = TelegramSessionInventoryService(
        db=db,
        auth_service=telegram_auth_service,
    )
    listener_topology_service = ListenerTopologyService(
        db=db,
        source_provider=FeatureGatedSourceProvider(
            source_registry,
            release_controls=release_controls,
        ),
        adapter_registry=adapter_registry,
        telegram_auth_service=telegram_auth_service,
        topology_store=listener_topology_store,
    )
    audience_tag_service = AudienceTagService(audience_tag_repo)
    content_publisher = _content_publisher(settings)
    content_automation_service = ContentAutomationService(
        repository=content_repo,
        publisher=content_publisher,
        daily_post_limit=settings.content_daily_post_limit,
    )
    if settings.environment != "test":
        announcement_repo: Any = LazyAsyncpgParentChildAnnouncementRepository(
            settings.database_url,
            config=dedup_scoring_config_from_settings(settings),
        )
    else:
        announcement_repo = InMemoryAnnouncementRepository(
            config=dedup_scoring_config_from_settings(settings)
        )

    manual_review_queue = ManualReviewQueueService(announcement_repo)
    post_ai_dedup_processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(
            candidate_repository=announcement_repo,
            config=dedup_scoring_config_from_settings(settings),
        ),
        announcement_repository=announcement_repo,
        manual_review_queue=manual_review_queue,
        technical_recorder=technical_recorder,
    )
    notification_matching_engine = NotificationMatchingEngine(
        filter_repository=SQLAlchemyNotificationSavedFilterRepository(db.session),
        user_repository=SQLAlchemyNotificationUserRepository(db.session),
        job_repository=SQLAlchemyNotificationJobRepository(db.session),
        queue_publisher=_noop_queue_publisher(),
        ops_notifier=ops_notifier,
    )
    notification_delivery_repository = SQLAlchemyNotificationDeliveryRepository(db.session)
    notification_bot = (
        _notification_bot(settings)
        if role == "worker" and settings.feature_notifications_enabled
        else None
    )
    notification_worker = (
        _notification_delivery_worker(
            repository=notification_delivery_repository,
            bot=notification_bot,
            analytics_recorder=analytics_recorder,
            technical_recorder=technical_recorder,
            ops_notifier=ops_notifier,
        )
        if notification_bot is not None
        else None
    )

    queue_consumer = create_queue_consumer_worker(
        settings=settings,
        redis_event_queue=event_queue,
        post_ai_processor=post_ai_dedup_processor,
        ops_notifier=ops_notifier,
        technical_recorder=technical_recorder,
        audience_tag_service=audience_tag_service,
        notification_worker=notification_worker,
        release_controls=release_controls,
        stage=cast(
            QueueStage,
            settings.worker_stage
            if settings.worker_stage in {"all", "pre_ai", "ai", "dedup", "notification"}
            else "all",
        ),
    )

    bot_controller = BotController(
        user_service=user_service,
        referral_service=referral_service,
        source_suggestion_service=source_suggestion_service,
        analytics_recorder=analytics_recorder,
        release_controls=release_controls,
        mini_app_url=settings.telegram_mini_app_url,
        bot_username=settings.telegram_bot_username,
    )

    return EstateFlowRuntime(
        settings=settings,
        db=db,
        redis=redis,
        adapter_registry=adapter_registry,
        listener_refresh_store=listener_refresh_store,
        listener_coordinator=listener_coordinator,
        queue_consumer=queue_consumer,
        event_queue=event_queue,
        health_checker=health_checker,
        release_controls=release_controls,
        ops_notifier=ops_notifier,
        analytics_recorder=analytics_recorder,
        technical_recorder=technical_recorder,
        source_registry=source_registry,
        listener_topology_service=listener_topology_service,
        telegram_session_inventory_service=telegram_session_inventory_service,
        website_scraper_service=website_scraper_service,
        search_service=search_service,
        saved_filter_service=saved_filter_service,
        source_suggestion_service=source_suggestion_service,
        telegram_auth_service=telegram_auth_service,
        audience_tag_service=audience_tag_service,
        content_automation_service=content_automation_service,
        metrics_service=metrics_service,
        user_service=user_service,
        referral_service=referral_service,
        manual_review_queue=manual_review_queue,
        post_ai_dedup_processor=post_ai_dedup_processor,
        notification_matching_engine=notification_matching_engine,
        notification_delivery_repository=notification_delivery_repository,
        notification_bot=notification_bot,
        bot_controller=bot_controller,
        nlp_search_extractor=nlp_search_extractor,
    )


def _build_nlp_search_extractor(
    settings: Settings,
    ops_notifier: OpsNotificationService,
) -> NlpSearchExtractor | None:
    if settings.openrouter_api_key is None:
        return None
    return NlpSearchExtractor(
        llm_client=create_openrouter_llm_client(settings=settings, ops_notifier=ops_notifier),
        min_confidence=settings.ai_min_confidence,
    )


def _ops_notifier(settings: Settings) -> OpsNotificationService:
    if settings.ops_bot_token is None or not settings.ops_chat_id:
        return DisabledOpsNotificationService()
    return TelegramOpsNotificationService(
        bot_token=settings.ops_bot_token,
        chat_id=settings.ops_chat_id,
    )


def _content_publisher(settings: Settings) -> ContentPublisher:
    if settings.content_channel_bot_token is None or not settings.content_channel_chat_id:
        return DryRunContentPublisher()
    return TelegramContentPublisher(
        bot_token=settings.content_channel_bot_token,
        chat_id=settings.content_channel_chat_id,
    )


class _NoopQueuePublisher:
    async def publish(self, job: object) -> None:  # pragma: no cover - runtime fallback
        return None


def _noop_queue_publisher() -> _NoopQueuePublisher:
    return _NoopQueuePublisher()


class _AiogramTelegramNotificationClient:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        markup = (
            InlineKeyboardMarkup.model_validate(reply_markup) if reply_markup is not None else None
        )
        try:
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=markup,
            )
        except TelegramRetryAfter as exc:
            raise TelegramRateLimitError(retry_after_seconds=exc.retry_after) from exc
        except TelegramForbiddenError as exc:
            raise TelegramUserBlockedError(str(exc)) from exc
        except (TelegramBadRequest, TelegramNotFound, TelegramUnauthorizedError) as exc:
            raise TelegramPermanentError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError, RestartingTelegram) as exc:
            raise TelegramTransientError(str(exc)) from exc
        return TelegramSendResult(message_id=str(message.message_id))


def _notification_bot(settings: Settings) -> Bot | None:
    if settings.telegram_bot_token is None:
        return None
    return Bot(token=settings.telegram_bot_token.get_secret_value())


def _notification_delivery_worker(
    *,
    repository: NotificationDeliveryRepository,
    bot: Bot,
    analytics_recorder: AnalyticsRecorder | None,
    technical_recorder: TechnicalMetricRecorder | None,
    ops_notifier: OpsNotificationService,
) -> NotificationDeliveryWorker:
    return NotificationDeliveryWorker(
        repository=repository,
        telegram=_AiogramTelegramNotificationClient(bot),
        analytics_recorder=analytics_recorder,
        technical_recorder=technical_recorder,
        ops_notifier=ops_notifier,
    )


def _adapter_plugin_dir(settings: Settings) -> Path:
    if settings.adapter_plugin_dir:
        return Path(settings.adapter_plugin_dir)
    return Path(__file__).resolve().parents[1] / "adapters" / "plugins"
