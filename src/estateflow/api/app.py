from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from estateflow.api.admin import router as admin_router
from estateflow.api.health import router as health_router
from estateflow.api.internal import router as internal_router
from estateflow.api.middleware import CorrelationIdMiddleware
from estateflow.api.search import router as search_router
from estateflow.api.webapp import router as webapp_router
from estateflow.application.core.config import Settings, get_settings
from estateflow.application.core.exceptions import EstateFlowError, ValidationErrorDetail
from estateflow.application.core.logging import configure_logging
from estateflow.application.runtime import EstateFlowRuntime, build_runtime
from estateflow.services.health import HealthChecker
from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.telegram_webapp_auth import TelegramWebAppAuthService

ApiProfile = Literal["all", "public", "admin", "internal"]


def create_app(
    settings: Settings | None = None,
    *,
    runtime: EstateFlowRuntime | None = None,
    health_checker: HealthChecker | None = None,
    ops_notifier: OpsNotificationService | None = None,
    profile: ApiProfile = "all",
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    active_runtime = runtime or build_runtime(resolved_settings, role="api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await active_runtime.aclose()

    app = FastAPI(
        title="EstateFlow API",
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.api_profile = profile
    app.state.webapp_auth_service = TelegramWebAppAuthService(resolved_settings)
    _install_runtime(app, active_runtime, health_checker=health_checker)
    if ops_notifier is not None:
        app.state.ops_notifier = ops_notifier
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health_router)
    if profile in {"all", "public"}:
        app.include_router(search_router)
        app.include_router(webapp_router)
    if profile in {"all", "admin"}:
        app.include_router(admin_router)
    if profile in {"all", "internal"}:
        app.include_router(internal_router)
    _register_exception_handlers(app)
    return app


def _install_runtime(
    app: FastAPI,
    runtime: EstateFlowRuntime,
    *,
    health_checker: HealthChecker | None = None,
) -> None:
    app.state.runtime = runtime
    app.state.db = runtime.db
    app.state.redis = runtime.redis
    app.state.adapter_registry = runtime.adapter_registry
    app.state.listener_refresh_store = runtime.listener_refresh_store
    app.state.listener_coordinator = runtime.listener_coordinator
    app.state.listener_topology_service = runtime.listener_topology_service
    app.state.telegram_session_inventory_service = runtime.telegram_session_inventory_service
    app.state.health_checker = health_checker or runtime.health_checker
    app.state.release_controls = runtime.release_controls
    app.state.ops_notifier = runtime.ops_notifier
    app.state.analytics_recorder = runtime.analytics_recorder
    app.state.technical_recorder = runtime.technical_recorder
    app.state.source_registry = runtime.source_registry
    app.state.website_scraper_service = runtime.website_scraper_service
    app.state.metrics_service = runtime.metrics_service
    app.state.search_service = runtime.search_service
    app.state.nlp_search_extractor = runtime.nlp_search_extractor
    app.state.saved_filter_service = runtime.saved_filter_service
    app.state.source_suggestion_service = runtime.source_suggestion_service
    app.state.telegram_auth_service = runtime.telegram_auth_service
    app.state.audience_tag_service = runtime.audience_tag_service
    app.state.content_automation_service = runtime.content_automation_service
    app.state.user_service = runtime.user_service
    app.state.referral_service = runtime.referral_service
    app.state.manual_review_queue = runtime.manual_review_queue
    app.state.post_ai_dedup_processor = runtime.post_ai_dedup_processor
    app.state.notification_matching_engine = runtime.notification_matching_engine
    app.state.notification_delivery_repository = runtime.notification_delivery_repository
    app.state.bot_controller = runtime.bot_controller


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(EstateFlowError)
    async def estateflow_error_handler(_request: Request, exc: EstateFlowError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ValidationErrorDetail(
                location=list(error["loc"]),
                message=str(error["msg"]),
            ).model_dump()
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": details,
                }
            },
        )
