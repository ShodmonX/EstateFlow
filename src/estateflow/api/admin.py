# ruff: noqa: I001
from __future__ import annotations

import base64
import secrets
from dataclasses import asdict
from datetime import date
from typing import Any, Literal, cast

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from estateflow.adapters.contracts import AdapterLoadResult, AdapterReloadReport
from estateflow.adapters.registry import AdapterRegistry
from estateflow.application.core.correlation import new_correlation_id
from estateflow.application.core.exceptions import EstateFlowError
from estateflow.application.core.redaction import redact_text
from estateflow.services.ai_client import (
    LLMAllModelsFailedError,
    LLMMessage,
    LLMRequest,
    create_openrouter_llm_client,
)
from estateflow.services.extraction import (
    ListingExtractionRaw,
    build_listing_prompt,
    listing_json_schema,
    normalize_listing,
)
from estateflow.services.media_storage import sniff_mime_type
from estateflow.services.ops_notifications import DisabledOpsNotificationService

from estateflow.services.analytics import (
    MetricRatio,
    ProductEventBucket,
    ProductMetricsService,
    TechnicalMetricSnapshot,
)
from estateflow.services.audience_tags import AudienceTagService, AudienceTagType
from estateflow.services.content_automation import ContentAutomationService, ContentPost
from estateflow.services.post_ai_dedup import (
    ManualReviewAdminService,
    ManualReviewItem,
    ManualReviewQueueService,
    ReviewReason,
    ReviewStatus,
)
from estateflow.services.release_controls import (
    FEATURE_FLAG_NAMES,
    FeatureFlagName,
    ReleaseControlService,
)
from estateflow.services.listener_status import ListenerTopologyService, ListenerTopologyStatus
from estateflow.services.telegram_auth import TelegramAuthService
from estateflow.services.telegram_sessions import (
    TelegramSessionInspection,
    TelegramSessionInventoryService,
)
from estateflow.services.source_config import SourceType
from estateflow.services.source_suggestions import SourceSuggestion, SourceSuggestionService

router = APIRouter(prefix="/admin", tags=["admin"])


class PageMetadata(BaseModel):
    returned: int
    limit: int
    offset: int
    total: int
    empty: bool


class AiTestExtractResponse(BaseModel):
    success: bool
    model_used: str
    confidence: float
    media_count: int
    extracted_raw: dict[str, Any]
    canonical: dict[str, Any]
    attempts: list[dict[str, Any]]


class SourceSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    suggestion_id: str
    source_identifier: str
    user_id: int | None
    source_type: str
    status: str
    admin_note: str | None
    reward_granted: bool
    source_id: str | None


class SourceSuggestionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_identifier: str
    source_type: Literal["telegram_channel", "telegram_group"] = "telegram_channel"


class SourceEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    identifier: str = Field(min_length=1)
    name: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=3, max_length=80)
    adapter_name: str | None = None
    source_profile: str | None = None


class SourceEnableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    name: str
    source_type: str
    identifier: str
    enabled: bool
    session_name: str | None
    adapter_name: str | None
    source_profile: str | None
    listener_account_key: str | None
    created: bool
    refresh_version: int


class ListenerRefreshResponse(BaseModel):
    refresh_version: int


class ListenerTopologyGroupResponse(BaseModel):
    adapter_name: str
    source_profile: str | None
    source_count: int
    source_ids: list[str]
    source_identifiers: list[str]
    source_names: list[str]
    runnable: bool
    error: str | None


class ListenerSnapshotResponse(BaseModel):
    generated_at: str
    refresh_version: int
    listener_account_key: str
    started: bool
    reason: str
    source_count: int
    telegram_source_count: int
    listener_count: int
    adapter_failures: list[str]
    groups: list[ListenerTopologyGroupResponse]


class ListenerAccountStatusResponse(BaseModel):
    account_key: str
    enabled: bool
    health_status: str
    last_successful_event_at: str | None
    flood_wait_count: int
    assigned_source_count: int
    session_name_hint: str
    session_path_hint: str


class ListenerAssignmentStatusResponse(BaseModel):
    source_id: str
    account_key: str
    assigned_at: str
    active: bool
    released_at: str | None
    reason: str


class ListenerSourceStatusResponse(BaseModel):
    source_id: str
    name: str
    source_type: str
    identifier: str
    enabled: bool
    adapter_name: str | None
    source_profile: str | None
    listener_account_key: str | None
    assigned_account_key: str | None
    assignment_active: bool
    assignment_reason: str | None
    session_name_hint: str | None
    session_path_hint: str | None


class ListenerTopologyStatusResponse(BaseModel):
    generated_at: str
    refresh_version: int
    session_dir: str
    adapter_report: AdapterReloadReport
    worker_snapshot: ListenerSnapshotResponse | None
    sources: list[ListenerSourceStatusResponse]
    listener_accounts: list[ListenerAccountStatusResponse]
    assignments: list[ListenerAssignmentStatusResponse]


class AdminDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=3, max_length=128)
    note: str | None = None
    expected_status: str = "pending"


class AudienceTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_key: str
    display_name_uz: str
    status: str
    usage_count: int


class AudienceTagDecisionRequest(AdminDecisionRequest):
    display_name_uz: str | None = None
    target_tag_key: str | None = None


class ManualReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id: str
    announcement_id: str
    reason: str
    related_candidate_id: str | None
    status: str
    created_at: str
    decided_at: str | None


class ManualReviewDetailResponse(ManualReviewResponse):
    score_breakdown: list[dict[str, object]]
    listing_summary: dict[str, object] | None = None
    candidate_listing_summary: dict[str, object] | None = None


class ManualReviewActionRequest(AdminDecisionRequest):
    action: Literal["mark_as_new", "reject", "merge_duplicate"]


class SourceSuggestionPage(BaseModel):
    items: list[SourceSuggestionResponse]
    metadata: PageMetadata


class AudienceTagPage(BaseModel):
    items: list[AudienceTagResponse]
    metadata: PageMetadata


class ManualReviewPage(BaseModel):
    items: list[ManualReviewResponse]
    metadata: PageMetadata


class ContentPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    post_id: str
    kind: str
    idempotency_key: str
    text: str
    status: str
    publisher_message_id: str | None


class MetricRatioResponse(BaseModel):
    numerator: int
    denominator: int
    rate: float | None
    missing_data: str


class BetaMetricsResponse(BaseModel):
    activation_rate: MetricRatioResponse
    d7_retention: MetricRatioResponse
    referral_conversion: MetricRatioResponse
    notification_engagement: MetricRatioResponse
    timezone: str
    start_at: str
    end_at: str
    d7_window_hours: int
    product_event_buckets: list[dict[str, object]]
    technical_metrics: dict[str, object] | None
    generated_at: str


class FeatureFlagStatusResponse(BaseModel):
    flags: dict[str, bool]
    versions: dict[str, int]
    updated_at: dict[str, str]
    beta_allowlist_size: int
    audit_events: list[dict[str, object]]


class FeatureFlagUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_name: FeatureFlagName
    enabled: bool
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)


class BetaAllowlistAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)


class BetaAllowlistAddResponse(BaseModel):
    user_id: int
    allowlist_size: int


class TelegramAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_name: str = Field(min_length=3, max_length=80)
    phone_number: str = Field(min_length=5, max_length=32)
    force_sms: bool = False


class TelegramAuthCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_name: str = Field(min_length=3, max_length=80)
    code: str = Field(min_length=4, max_length=12)


class TelegramAuthPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_name: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class TelegramAuthStartResponse(BaseModel):
    session_name: str
    phone_number: str
    code_sent: bool
    already_authorized: bool
    authorized: bool


class TelegramAuthCodeResponse(BaseModel):
    session_name: str
    authorized: bool
    requires_password: bool


class TelegramAuthPasswordResponse(BaseModel):
    session_name: str
    authorized: bool


class TelegramAuthStateResponse(BaseModel):
    session_name: str
    phone_number: str
    phone_code_hash_present: bool
    created_at: str
    updated_at: str
    requires_password: bool


class TelegramSessionSourceBindingResponse(BaseModel):
    source_id: str
    source_type: str
    identifier: str
    name: str
    source_profile: str | None
    enabled: bool
    session_name: str


class TelegramSessionStatusResponse(BaseModel):
    session_name: str
    session_path: str
    session_file_path: str
    file_exists: bool
    file_size_bytes: int | None
    authorized: bool | None
    auth_state_present: bool
    requires_password: bool
    updated_at: str | None
    status: str
    error: str | None
    bound_sources: list[TelegramSessionSourceBindingResponse]


@router.get("/source-suggestions/pending", response_model=SourceSuggestionPage)
async def list_pending_source_suggestions(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    limit: int = 20,
    offset: int = 0,
) -> SourceSuggestionPage:
    _admin_auth(request=request, token=x_admin_token)
    service = _source_suggestion_service(request)
    items = list(await service.list_pending())
    page = _paginate(items, limit=limit, offset=offset)
    return SourceSuggestionPage(items=page[0], metadata=page[1])


@router.get("/source-suggestions/{suggestion_id}", response_model=SourceSuggestionResponse)
async def get_source_suggestion(
    suggestion_id: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> SourceSuggestion:
    _admin_auth(request=request, token=x_admin_token)
    item = await _source_suggestion_service(request).get(suggestion_id=suggestion_id)
    if item is None:
        raise EstateFlowError(
            code="source_suggestion_not_found",
            message="Source suggestion was not found.",
            status_code=404,
        )
    return item


@router.post("/source-suggestions/{suggestion_id}/approve", response_model=SourceSuggestionResponse)
async def approve_source_suggestion(
    suggestion_id: str,
    request: Request,
    body: AdminDecisionRequest,
    x_admin_token: str | None = Header(default=None),
) -> SourceSuggestion:
    _admin_auth(request=request, token=x_admin_token)
    try:
        result = await _source_suggestion_service(request).approve(
            suggestion_id=suggestion_id,
            admin_user_id=_admin_actor_user_id(request),
            idempotency_key=body.idempotency_key,
            note=body.note,
            expected_status=_source_status(body.expected_status),
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="source_suggestion_not_found",
            message="Source suggestion was not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="source_suggestion_state_conflict",
            message="Source suggestion state precondition failed.",
            status_code=409,
        ) from exc
    return result.suggestion


@router.post("/source-suggestions/{suggestion_id}/reject", response_model=SourceSuggestionResponse)
async def reject_source_suggestion(
    suggestion_id: str,
    request: Request,
    body: AdminDecisionRequest,
    x_admin_token: str | None = Header(default=None),
) -> SourceSuggestion:
    _admin_auth(request=request, token=x_admin_token)
    try:
        result = await _source_suggestion_service(request).reject(
            suggestion_id=suggestion_id,
            admin_user_id=_admin_actor_user_id(request),
            idempotency_key=body.idempotency_key,
            note=body.note,
            expected_status=_source_status(body.expected_status),
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="source_suggestion_not_found",
            message="Source suggestion was not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="source_suggestion_state_conflict",
            message="Source suggestion state precondition failed.",
            status_code=409,
        ) from exc
    return result.suggestion


@router.get("/audience-tags/pending", response_model=AudienceTagPage)
async def list_pending_audience_tags(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    limit: int = 20,
    offset: int = 0,
) -> AudienceTagPage:
    _admin_auth(request=request, token=x_admin_token)
    service = _audience_tag_service(request)
    items = list(await service.list_pending())
    page = _paginate(items, limit=limit, offset=offset)
    return AudienceTagPage(items=page[0], metadata=page[1])


@router.get("/audience-tags/{tag_key}", response_model=AudienceTagResponse)
async def get_audience_tag(
    tag_key: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> AudienceTagType:
    _admin_auth(request=request, token=x_admin_token)
    try:
        tag = await _audience_tag_service(request).get(tag_key=tag_key)
    except ValueError as exc:
        raise EstateFlowError(code="invalid_audience_tag", message=str(exc)) from exc
    if tag is None:
        raise EstateFlowError(
            code="audience_tag_not_found",
            message="Audience tag was not found.",
            status_code=404,
        )
    return tag


@router.post("/audience-tags/{tag_key}/approve", response_model=AudienceTagResponse)
async def approve_audience_tag(
    tag_key: str,
    request: Request,
    body: AudienceTagDecisionRequest,
    x_admin_token: str | None = Header(default=None),
) -> AudienceTagType:
    _admin_auth(request=request, token=x_admin_token)
    try:
        return await _audience_tag_service(request).approve(
            tag_key=tag_key,
            admin_user_id=_admin_actor_user_id(request),
            idempotency_key=body.idempotency_key,
            display_name_uz=body.display_name_uz,
            expected_status=_audience_status(body.expected_status),
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="audience_tag_not_found",
            message="Audience tag was not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="audience_tag_state_conflict",
            message="Audience tag state precondition failed.",
            status_code=409,
        ) from exc


@router.post("/audience-tags/{tag_key}/reject", response_model=AudienceTagResponse)
async def reject_audience_tag(
    tag_key: str,
    request: Request,
    body: AudienceTagDecisionRequest,
    x_admin_token: str | None = Header(default=None),
) -> AudienceTagType:
    _admin_auth(request=request, token=x_admin_token)
    try:
        return await _audience_tag_service(request).reject(
            tag_key=tag_key,
            admin_user_id=_admin_actor_user_id(request),
            idempotency_key=body.idempotency_key,
            note=body.note,
            expected_status=_audience_status(body.expected_status),
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="audience_tag_not_found",
            message="Audience tag was not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="audience_tag_state_conflict",
            message="Audience tag state precondition failed.",
            status_code=409,
        ) from exc


@router.post("/audience-tags/{tag_key}/merge", response_model=AudienceTagResponse)
async def merge_audience_tag(
    tag_key: str,
    request: Request,
    body: AudienceTagDecisionRequest,
    x_admin_token: str | None = Header(default=None),
) -> AudienceTagType:
    _admin_auth(request=request, token=x_admin_token)
    if body.target_tag_key is None:
        raise EstateFlowError(
            code="target_tag_required",
            message="target_tag_key is required for merge.",
        )
    try:
        return await _audience_tag_service(request).merge(
            tag_key=tag_key,
            target_tag_key=body.target_tag_key,
            admin_user_id=_admin_actor_user_id(request),
            idempotency_key=body.idempotency_key,
            note=body.note,
            expected_status=_audience_status(body.expected_status),
        )
    except (KeyError, ValueError) as exc:
        raise EstateFlowError(
            code="audience_tag_merge_failed",
            message=str(exc),
            status_code=400,
        ) from exc


@router.get("/manual-review/pending", response_model=ManualReviewPage)
async def list_pending_manual_review(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    reason: ReviewReason | None = None,
    limit: int = 20,
    offset: int = 0,
) -> ManualReviewPage:
    _admin_auth(request=request, token=x_admin_token)
    review_queue = _manual_review_queue(request)
    items = list(
        await review_queue.list_items(
            status="pending",
            reason=reason,
            limit=limit,
            offset=offset,
        )
    )
    total = await review_queue.count_items(status="pending", reason=reason)
    return ManualReviewPage(
        items=[_manual_review_response(item) for item in items],
        metadata=PageMetadata(
            returned=len(items),
            limit=_limit(limit),
            offset=_offset(offset),
            total=total,
            empty=total == 0,
        ),
    )


@router.get("/manual-review/{review_id}", response_model=ManualReviewDetailResponse)
async def get_manual_review(
    review_id: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> ManualReviewDetailResponse:
    _admin_auth(request=request, token=x_admin_token)
    review_queue = _manual_review_queue(request)
    item = await review_queue.get_item(review_id)
    if item is None:
        raise EstateFlowError(
            code="manual_review_not_found",
            message="Manual review item was not found.",
            status_code=404,
        )
    return await _manual_review_detail(request, item)


@router.post("/manual-review/{review_id}/actions", response_model=ManualReviewResponse)
async def act_on_manual_review(
    review_id: str,
    request: Request,
    body: ManualReviewActionRequest,
    x_admin_token: str | None = Header(default=None),
) -> ManualReviewResponse:
    _admin_auth(request=request, token=x_admin_token)
    admin = ManualReviewAdminService(_manual_review_queue(request))
    expected_status = _review_status(body.expected_status)
    admin_user_id = _admin_actor_user_id(request)
    try:
        if body.action == "mark_as_new":
            item = await admin.mark_as_new(
                review_id,
                admin_user_id=admin_user_id,
                idempotency_key=body.idempotency_key,
                expected_status=expected_status,
                note=body.note,
            )
        elif body.action == "reject":
            item = await admin.reject(
                review_id,
                admin_user_id=admin_user_id,
                idempotency_key=body.idempotency_key,
                expected_status=expected_status,
                note=body.note,
            )
        else:
            item = await admin.merge_related_duplicate(
                review_id,
                admin_user_id=admin_user_id,
                idempotency_key=body.idempotency_key,
                expected_status=expected_status,
                note=body.note,
            )
    except KeyError as exc:
        raise EstateFlowError(
            code="manual_review_not_found",
            message="Manual review item was not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="manual_review_state_conflict",
            message=str(exc),
            status_code=409,
        ) from exc
    return _manual_review_response(item)


@router.post("/content/daily", response_model=list[ContentPostResponse])
async def generate_daily_content(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> tuple[ContentPost, ...]:
    _admin_auth(request=request, token=x_admin_token)
    service: ContentAutomationService | None = getattr(
        request.app.state,
        "content_automation_service",
        None,
    )
    if service is None:
        raise EstateFlowError(
            code="content_automation_unavailable",
            message="Content automation service is not configured.",
            status_code=503,
        )
    return await service.generate_daily()


@router.get("/metrics/beta", response_model=BetaMetricsResponse)
async def beta_metrics(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    start: str = "2026-08-01",
    end: str = "2026-08-08",
    timezone_name: str = "Asia/Tashkent",
    d7_window_hours: int = 24,
) -> BetaMetricsResponse:
    _admin_auth(request=request, token=x_admin_token)
    service: ProductMetricsService | None = getattr(request.app.state, "metrics_service", None)
    if service is None:
        raise EstateFlowError(
            code="metrics_unavailable",
            message="Product metrics service is not configured.",
            status_code=503,
        )
    try:
        snapshot = await service.beta_snapshot(
            start=date_from_query(start),
            end=date_from_query(end),
            timezone_name=timezone_name,
            d7_window_hours=d7_window_hours,
        )
    except ValueError as exc:
        raise EstateFlowError(code="invalid_metrics_window", message=str(exc)) from exc
    return BetaMetricsResponse(
        activation_rate=_metric_ratio(snapshot.activation_rate),
        d7_retention=_metric_ratio(snapshot.d7_retention),
        referral_conversion=_metric_ratio(snapshot.referral_conversion),
        notification_engagement=_metric_ratio(snapshot.notification_engagement),
        timezone=snapshot.timezone,
        start_at=snapshot.start_at.isoformat(),
        end_at=snapshot.end_at.isoformat(),
        d7_window_hours=snapshot.d7_window_hours,
        product_event_buckets=[
            _product_event_bucket(item) for item in snapshot.product_event_buckets
        ],
        technical_metrics=(
            None
            if snapshot.technical_metrics is None
            else _technical_snapshot(snapshot.technical_metrics)
        ),
        generated_at=snapshot.generated_at.isoformat(),
    )


@router.get("/release/flags", response_model=FeatureFlagStatusResponse)
async def release_flags(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> FeatureFlagStatusResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _release_controls(request)
    snapshot = await service.snapshot()
    audit_events = await service.audit_log()
    return FeatureFlagStatusResponse(
        flags={name: snapshot.enabled(name) for name in FEATURE_FLAG_NAMES},
        versions={name: snapshot.version(name) for name in FEATURE_FLAG_NAMES},
        updated_at={name: snapshot.updated_at(name).isoformat() for name in FEATURE_FLAG_NAMES},
        beta_allowlist_size=await service.beta_allowlist_size(),
        audit_events=[
            {
                "flag_name": event.flag_name,
                "previous_enabled": event.previous_enabled,
                "new_enabled": event.new_enabled,
                "previous_version": event.previous_version,
                "new_version": event.new_version,
                "actor": event.actor,
                "reason": event.reason,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in audit_events
        ],
    )


@router.post("/release/flags", response_model=FeatureFlagStatusResponse)
async def update_release_flag(
    request: Request,
    body: FeatureFlagUpdateRequest,
    x_admin_token: str | None = Header(default=None),
) -> FeatureFlagStatusResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _release_controls(request)
    try:
        await service.set_flag(
            name=body.flag_name,
            enabled=body.enabled,
            expected_version=body.expected_version,
            actor=_admin_actor_label(request),
            reason=body.reason,
        )
    except ValueError as exc:
        msg = str(exc)
        status_code = (
            409
            if "conflict" in msg.lower()
            or "mismatch" in msg.lower()
            or "stale" in msg.lower()
            or "expected version" in msg.lower()
            else 400
        )
        raise EstateFlowError(
            code="invalid_feature_flag_change",
            message=msg,
            status_code=status_code,
    ) from exc
    return await release_flags(request=request, x_admin_token=x_admin_token)


@router.post("/release/beta-allowlist", response_model=BetaAllowlistAddResponse)
async def add_beta_allowlist_user(
    request: Request,
    body: BetaAllowlistAddRequest,
    x_admin_token: str | None = Header(default=None),
) -> BetaAllowlistAddResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _release_controls(request)
    try:
        size = await service.add_beta_allowlist_user_id(user_id=body.user_id)
    except ValueError as exc:
        raise EstateFlowError(
            code="invalid_beta_allowlist_user",
            message=str(exc),
            status_code=400,
        ) from exc
    return BetaAllowlistAddResponse(user_id=body.user_id, allowlist_size=size)


@router.get("/adapters", response_model=AdapterReloadReport)
async def list_adapters(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> AdapterReloadReport:
    _admin_auth(request=request, token=x_admin_token)
    registry = _adapter_registry(request)
    return _adapter_report(registry)


@router.post("/adapters/reload", response_model=AdapterReloadReport)
async def reload_adapters(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> AdapterReloadReport:
    _admin_auth(request=request, token=x_admin_token)
    registry = _adapter_registry(request)
    report = await registry.reload()
    refresh_store = _listener_refresh_store(request)
    await refresh_store.signal()
    return report


@router.post("/telegram/auth/start", response_model=TelegramAuthStartResponse)
async def start_telegram_auth(
    request: Request,
    body: TelegramAuthStartRequest,
    x_admin_token: str | None = Header(default=None),
) -> TelegramAuthStartResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_auth_service(request)
    try:
        result = await service.start_login(
            session_name=body.session_name,
            phone_number=body.phone_number,
            force_sms=body.force_sms,
        )
    except ValueError as exc:
        raise EstateFlowError(
            code="telegram_auth_invalid_input",
            message=str(exc),
            status_code=400,
        ) from exc
    except RuntimeError as exc:
        msg = str(exc)
        status_code = (
            409 if "locked" in msg.lower() else (429 if "rate limited" in msg.lower() else 503)
        )
        raise EstateFlowError(
            code="telegram_auth_unavailable",
            message=msg,
            status_code=status_code,
        ) from exc
    return TelegramAuthStartResponse(
        session_name=result.session_name,
        phone_number=result.phone_number,
        code_sent=result.code_sent,
        already_authorized=result.already_authorized,
        authorized=result.already_authorized,
    )


@router.post("/telegram/auth/confirm-code", response_model=TelegramAuthCodeResponse)
async def confirm_telegram_auth_code(
    request: Request,
    body: TelegramAuthCodeRequest,
    x_admin_token: str | None = Header(default=None),
) -> TelegramAuthCodeResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_auth_service(request)
    try:
        result = await service.confirm_code(session_name=body.session_name, code=body.code)
    except KeyError as exc:
        raise EstateFlowError(
            code="telegram_auth_session_not_found",
            message="Login session not found or expired.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="telegram_auth_invalid_code",
            message=str(exc),
            status_code=400,
        ) from exc
    except RuntimeError as exc:
        msg = str(exc)
        status_code = (
            409 if "locked" in msg.lower() else (429 if "rate limited" in msg.lower() else 409)
        )
        raise EstateFlowError(
            code="telegram_auth_failed",
            message=msg,
            status_code=status_code,
        ) from exc
    refresh_store = _listener_refresh_store(request)
    await refresh_store.signal()
    return TelegramAuthCodeResponse(
        session_name=result.session_name,
        authorized=result.authorized,
        requires_password=result.requires_password,
    )


@router.get("/telegram/auth/state/{session_name}", response_model=TelegramAuthStateResponse)
async def telegram_auth_state(
    session_name: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> TelegramAuthStateResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_auth_service(request)
    state = await service.get_state(session_name)
    if state is None:
        raise EstateFlowError(
            code="telegram_auth_session_not_found",
            message="Login session not found.",
            status_code=404,
        )
    return TelegramAuthStateResponse(
        session_name=state.session_name,
        phone_number=state.phone_number,
        phone_code_hash_present=state.phone_code_hash is not None,
        created_at=state.created_at.isoformat(),
        updated_at=state.updated_at.isoformat(),
        requires_password=state.requires_password,
    )


@router.delete("/telegram/auth/{session_name}")
async def delete_telegram_auth_session(
    session_name: str,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, bool]:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_auth_service(request)
    await service.delete_state(session_name)
    refresh_store = _listener_refresh_store(request)
    await refresh_store.signal()
    return {"deleted": True}


@router.get("/telegram/sessions", response_model=list[TelegramSessionStatusResponse])
async def list_telegram_sessions(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> list[TelegramSessionStatusResponse]:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_session_inventory_service(request)
    sessions = await service.list_sessions()
    return [_telegram_session_status_response(session) for session in sessions]


@router.post("/telegram/auth/confirm-password", response_model=TelegramAuthPasswordResponse)
async def confirm_telegram_auth_password(
    request: Request,
    body: TelegramAuthPasswordRequest,
    x_admin_token: str | None = Header(default=None),
) -> TelegramAuthPasswordResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _telegram_auth_service(request)
    try:
        result = await service.confirm_password(
            session_name=body.session_name,
            password=body.password,
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="telegram_auth_session_not_found",
            message="Login session not found or expired.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="telegram_auth_invalid_password",
            message=str(exc),
            status_code=400,
        ) from exc
    except RuntimeError as exc:
        msg = str(exc)
        status_code = (
            409 if "locked" in msg.lower() else (429 if "rate limited" in msg.lower() else 409)
        )
        raise EstateFlowError(
            code="telegram_auth_failed",
            message=msg,
            status_code=status_code,
        ) from exc
    refresh_store = _listener_refresh_store(request)
    await refresh_store.signal()
    return TelegramAuthPasswordResponse(
        session_name=result.session_name,
        authorized=result.authorized,
    )


@router.post("/sources/enable", response_model=SourceEnableResponse)
async def enable_source(
    request: Request,
    body: SourceEnableRequest,
    x_admin_token: str | None = Header(default=None),
) -> SourceEnableResponse:
    _admin_auth(request=request, token=x_admin_token)
    session_name = body.session_name.strip() if body.session_name else None
    if body.source_type in {"telegram_channel", "telegram_group"} and not session_name:
        raise EstateFlowError(
            code="telegram_session_name_required",
            message="session_name is required for Telegram sources.",
            status_code=422,
        )
    target_identifier = body.identifier
    if body.source_type in {"telegram_channel", "telegram_group"} and session_name is not None:
        try:
            access_check = await _telegram_auth_service(request).verify_source_access(
                session_name=session_name,
                source_identifier=body.identifier,
            )
            resolved_id = getattr(access_check, "resolved_channel_id", None)
            if resolved_id:
                target_identifier = resolved_id
        except PermissionError as exc:
            raise EstateFlowError(
                code="telegram_source_access_denied",
                message=str(exc),
                status_code=403,
            ) from exc
    registry = _source_registry(request)
    source, created = await registry.create_or_enable_source(
        source_type=body.source_type,
        identifier=target_identifier,
        name=body.name,
        adapter_name=body.adapter_name,
        source_profile=body.source_profile,
        session_name=session_name,
    )
    refresh_store = _listener_refresh_store(request)
    refresh_version = await refresh_store.signal()
    return SourceEnableResponse(
        source_id=source.source_id,
        name=source.name,
        source_type=source.source_type,
        identifier=source.identifier,
        enabled=source.enabled,
        session_name=source.listener_account_key,
        adapter_name=source.adapter_name,
        source_profile=getattr(source, "source_profile", None),
        listener_account_key=source.listener_account_key,
        created=created,
        refresh_version=refresh_version,
    )


@router.post("/listeners/refresh", response_model=ListenerRefreshResponse)
async def refresh_listeners(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> ListenerRefreshResponse:
    _admin_auth(request=request, token=x_admin_token)
    refresh_store = _listener_refresh_store(request)
    refresh_version = await refresh_store.signal()
    return ListenerRefreshResponse(refresh_version=refresh_version)


@router.get("/listeners/status", response_model=ListenerTopologyStatusResponse)
async def listener_status(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> ListenerTopologyStatusResponse:
    _admin_auth(request=request, token=x_admin_token)
    service = _listener_topology_service(request)
    status = await service.snapshot()
    return _listener_topology_response(status)


@router.post("/ai/test-extract", response_model=AiTestExtractResponse)
@router.post("/ai/extract-test", response_model=AiTestExtractResponse)
async def test_ai_extraction(
    request: Request,
    text: str = Form(default=""),
    file1: UploadFile | None = File(default=None, description="Image 1"),  # noqa: B008
    file2: UploadFile | None = File(default=None, description="Image 2"),  # noqa: B008
    file3: UploadFile | None = File(default=None, description="Image 3"),  # noqa: B008
    file4: UploadFile | None = File(default=None, description="Image 4"),  # noqa: B008
    file5: UploadFile | None = File(default=None, description="Image 5"),  # noqa: B008
    file6: UploadFile | None = File(default=None, description="Image 6"),  # noqa: B008
    file7: UploadFile | None = File(default=None, description="Image 7"),  # noqa: B008
    file8: UploadFile | None = File(default=None, description="Image 8"),  # noqa: B008
    file9: UploadFile | None = File(default=None, description="Image 9"),  # noqa: B008
    file10: UploadFile | None = File(default=None, description="Image 10"),  # noqa: B008
    x_admin_token: str | None = Header(default=None),
) -> AiTestExtractResponse:
    _admin_auth(request=request, token=x_admin_token)

    all_files: list[UploadFile] = []
    for single_file in (file1, file2, file3, file4, file5, file6, file7, file8, file9, file10):
        if single_file is not None and single_file.filename:
            all_files.append(single_file)

    if len(all_files) > 10:
        raise EstateFlowError(
            code="too_many_files",
            message="Maksimal 10 ta rasm yuborish mumkin.",
            status_code=400,
        )

    settings = request.app.state.settings
    if not settings.openrouter_api_key:
        raise EstateFlowError(
            code="ai_not_configured",
            message="OPENROUTER_API_KEY sozlanmagan.",
            status_code=503,
        )

    ops_notifier = (
        getattr(request.app.state, "ops_notifier", None) or DisabledOpsNotificationService()
    )
    llm_client = create_openrouter_llm_client(settings=settings, ops_notifier=ops_notifier)

    image_blocks: list[dict[str, Any]] = []
    for file in all_files:
        if not file.filename:
            continue
        content_bytes = await file.read()
        if not content_bytes:
            continue
        mime_type = file.content_type or sniff_mime_type(content_bytes) or "image/jpeg"
        b64 = base64.b64encode(content_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"
        image_blocks.append({"type": "image_url", "image_url": {"url": data_url}})

    vision_required = len(image_blocks) > 0
    media_count = len(image_blocks)

    audience_tag_service = getattr(request.app.state, "audience_tag_service", None)
    prompt_audience_tags = (
        await audience_tag_service.prompt_tags(limit=15)
        if audience_tag_service is not None
        else None
    )
    prompt_messages = build_listing_prompt(
        raw_text=text,
        prompt_version=settings.ai_prompt_version,
        vision_required=vision_required,
        media_count=media_count,
        audience_tags=prompt_audience_tags,
    )

    user_content: Any
    if image_blocks:
        user_content = [
            {"type": "text", "text": prompt_messages[1]["content"]},
            *image_blocks,
        ]
    else:
        user_content = prompt_messages[1]["content"]

    correlation_id = new_correlation_id()
    llm_request = LLMRequest(
        messages=[
            LLMMessage(role="system", content=prompt_messages[0]["content"]),
            LLMMessage(role="user", content=user_content),
        ],
        json_schema=listing_json_schema(),
        correlation_id=correlation_id,
        min_confidence=settings.ai_min_confidence,
    )

    try:
        response, attempts = await llm_client.complete_json(llm_request)
    except LLMAllModelsFailedError as exc:
        raise EstateFlowError(
            code="ai_extraction_failed",
            message="Barcha AI modellari muvaffaqiyatsiz yakunlandi.",
            details=[{"attempts": [asdict(a) for a in exc.attempts]}],
            status_code=502,
        ) from exc
    except Exception as exc:
        raise EstateFlowError(
            code="ai_extraction_error",
            message=f"AI extraction error: {exc}",
            status_code=500,
        ) from exc

    raw = ListingExtractionRaw.model_validate(response.content)
    canonical = normalize_listing(
        raw,
        prompt_version=settings.ai_prompt_version,
        source_text=text,
        vision_required=vision_required,
    )

    return AiTestExtractResponse(
        success=True,
        model_used=response.model,
        confidence=response.confidence,
        media_count=media_count,
        extracted_raw=raw.model_dump(mode="json"),
        canonical=canonical.model_dump(mode="json"),
        attempts=[asdict(a) for a in attempts],
    )


def _admin_auth(request: Request | None, token: str | None = None) -> None:
    if request is None:
        return None
    settings = request.app.state.settings
    expected = settings.admin_api_token.get_secret_value() if settings.admin_api_token else None
    if not expected or token is None or not secrets.compare_digest(token, expected):
        raise EstateFlowError(
            code="admin_unauthorized",
            message="Admin authorization failed.",
            status_code=403,
        )
    return None


def _admin_actor_user_id(request: Request) -> int:
    settings = request.app.state.settings
    return cast(int, settings.admin_actor_user_id)


def _admin_actor_label(request: Request) -> str:
    return f"server-admin:{_admin_actor_user_id(request)}"


def _adapter_registry(request: Request) -> AdapterRegistry:
    registry = cast(AdapterRegistry | None, getattr(request.app.state, "adapter_registry", None))
    if registry is None:
        raise EstateFlowError(
            code="adapter_registry_unavailable",
            message="Adapter registry is not configured.",
            status_code=503,
        )
    return registry


def _source_registry(request: Request) -> Any:
    registry = cast(Any, getattr(request.app.state, "source_registry", None))
    if registry is None:
        raise EstateFlowError(
            code="source_registry_unavailable",
            message="Source registry is not configured.",
            status_code=503,
        )
    return registry


def _listener_refresh_store(request: Request) -> Any:
    store = cast(Any, getattr(request.app.state, "listener_refresh_store", None))
    if store is None:
        raise EstateFlowError(
            code="listener_refresh_unavailable",
            message="Listener refresh store is not configured.",
            status_code=503,
        )
    return store


def _listener_topology_service(request: Request) -> ListenerTopologyService:
    service: ListenerTopologyService | None = getattr(
        request.app.state,
        "listener_topology_service",
        None,
    )
    if service is None:
        raise EstateFlowError(
            code="listener_topology_unavailable",
            message="Listener topology service is not configured.",
            status_code=503,
        )
    return service


def _adapter_report(registry: AdapterRegistry) -> AdapterReloadReport:
    return AdapterReloadReport(
        plugin_directory=str(registry.plugin_directory),
        loaded_count=len(registry.list_plugins()),
        failed_count=len(registry.list_failures()),
        loaded_plugins=[
            AdapterLoadResult(
                plugin_name=item.plugin_name,
                module_path=str(item.module_path),
                state="loaded",
                manifest=item.manifest,
            )
            for item in registry.list_plugins()
        ],
        failures=registry.list_failures(),
    )


def date_from_query(value: str) -> date:
    return date.fromisoformat(value)


def _metric_ratio(metric: MetricRatio) -> MetricRatioResponse:
    return MetricRatioResponse(
        numerator=metric.numerator,
        denominator=metric.denominator,
        rate=metric.rate,
        missing_data=metric.missing_data,
    )


def _product_event_bucket(bucket: ProductEventBucket) -> dict[str, object]:
    return {
        "bucket_start": bucket.bucket_start.isoformat(),
        "event_name": bucket.event_name,
        "count": bucket.count,
    }


def _technical_snapshot(snapshot: TechnicalMetricSnapshot) -> dict[str, object]:
    return {
        "queue_delay_avg_ms": snapshot.queue_delay_avg_ms,
        "queue_delay_samples": snapshot.queue_delay_samples,
        "ai_fallback_count": snapshot.ai_fallback_count,
        "ai_validation_failure_count": snapshot.ai_validation_failure_count,
        "dedup_duplicate_rate": snapshot.dedup_duplicate_rate,
        "dedup_duplicate_count": snapshot.dedup_duplicate_count,
        "dedup_total_count": snapshot.dedup_total_count,
        "notification_retry_count": snapshot.notification_retry_count,
        "notification_error_count": snapshot.notification_error_count,
        "listener_health_counts": snapshot.listener_health_counts,
    }


def _paginate(items: list[Any], *, limit: int, offset: int) -> tuple[list[Any], PageMetadata]:
    safe_limit = _limit(limit)
    safe_offset = _offset(offset)
    page = items[safe_offset : safe_offset + safe_limit]
    return page, PageMetadata(
        returned=len(page),
        limit=safe_limit,
        offset=safe_offset,
        total=len(items),
        empty=len(items) == 0,
    )


def _limit(value: int) -> int:
    if value < 1 or value > 100:
        raise EstateFlowError(
            code="invalid_pagination",
            message="limit must be between 1 and 100.",
            status_code=422,
        )
    return value


def _offset(value: int) -> int:
    if value < 0:
        raise EstateFlowError(
            code="invalid_pagination",
            message="offset must be greater than or equal to 0.",
            status_code=422,
        )
    return value


def _source_suggestion_service(request: Request) -> SourceSuggestionService:
    service: SourceSuggestionService | None = getattr(
        request.app.state,
        "source_suggestion_service",
        None,
    )
    if service is None:
        raise EstateFlowError(
            code="source_suggestions_unavailable",
            message="Source suggestion service is not configured.",
            status_code=503,
        )
    return service


def _audience_tag_service(request: Request) -> AudienceTagService:
    service: AudienceTagService | None = getattr(request.app.state, "audience_tag_service", None)
    if service is None:
        raise EstateFlowError(
            code="audience_tags_unavailable",
            message="Audience tag service is not configured.",
            status_code=503,
        )
    return service


def _manual_review_queue(request: Request) -> ManualReviewQueueService:
    review_queue: ManualReviewQueueService | None = getattr(
        request.app.state,
        "manual_review_queue",
        None,
    )
    if review_queue is None:
        raise EstateFlowError(
            code="manual_review_unavailable",
            message="Manual review queue is not configured.",
            status_code=503,
        )
    return review_queue


def _release_controls(request: Request) -> ReleaseControlService:
    service: ReleaseControlService | None = getattr(request.app.state, "release_controls", None)
    if service is None:
        raise EstateFlowError(
            code="release_controls_unavailable",
            message="Release controls service is not configured.",
            status_code=503,
        )
    return service


def _telegram_auth_service(request: Request) -> TelegramAuthService:
    service: TelegramAuthService | None = getattr(request.app.state, "telegram_auth_service", None)
    if service is None:
        raise EstateFlowError(
            code="telegram_auth_unavailable",
            message="Telegram auth service is not configured.",
            status_code=503,
        )
    return service


def _telegram_session_inventory_service(request: Request) -> TelegramSessionInventoryService:
    service: TelegramSessionInventoryService | None = getattr(
        request.app.state,
        "telegram_session_inventory_service",
        None,
    )
    if service is None:
        raise EstateFlowError(
            code="telegram_session_inventory_unavailable",
            message="Telegram session inventory service is not configured.",
            status_code=503,
        )
    return service


def _manual_review_response(item: ManualReviewItem) -> ManualReviewResponse:
    return ManualReviewResponse(
        review_id=item.review_id,
        announcement_id=item.announcement_id,
        reason=item.reason,
        related_candidate_id=item.related_candidate_id,
        status=item.status,
        created_at=item.created_at.isoformat(),
        decided_at=item.decided_at.isoformat() if item.decided_at is not None else None,
    )


async def _manual_review_detail(
    request: Request, item: ManualReviewItem
) -> ManualReviewDetailResponse:
    response = _manual_review_response(item)
    listing_summary = None
    candidate_listing_summary = None
    review_queue = _manual_review_queue(request)
    repository = getattr(review_queue, "_repository", None)
    announcement = None if repository is None else await repository.get(item.announcement_id)
    if announcement is not None:
        listing_summary = _announcement_summary(announcement)
    if repository is not None and item.related_candidate_id is not None:
        candidate = await repository.get(item.related_candidate_id)
        if candidate is not None:
            candidate_listing_summary = _announcement_summary(candidate)
    return ManualReviewDetailResponse(
        **response.model_dump(),
        score_breakdown=[
            {
                "name": signal.name,
                "weight": signal.weight,
                "matched": signal.matched,
                "reason": signal.reason,
                "details": signal.details,
            }
            for signal in item.score_breakdown
        ],
        listing_summary=listing_summary,
        candidate_listing_summary=candidate_listing_summary,
    )


def _announcement_summary(announcement: Any) -> dict[str, object]:
    canonical = announcement.canonical
    return {
        "announcement_id": announcement.announcement_id,
        "price": str(canonical.price) if canonical.price is not None else None,
        "currency": canonical.currency,
        "district": canonical.district,
        "rooms": canonical.rooms,
        "area_sqm": str(canonical.area_sqm) if canonical.area_sqm is not None else None,
        "floor": canonical.floor,
        "total_floors": canonical.total_floors,
        "phone_numbers": canonical.phone_numbers,
        "confidence": canonical.confidence,
        "description": _shorten(redact_text(canonical.description), limit=420),
        "source_url": announcement.source_url,
        "occurred_at": announcement.occurred_at.isoformat(),
    }


def _shorten(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _source_status(value: str) -> Literal["pending", "approved", "rejected"]:
    if value not in {"pending", "approved", "rejected"}:
        raise EstateFlowError(code="invalid_status", message="Invalid source status.")
    return value  # type: ignore[return-value]


def _audience_status(value: str) -> Literal["approved", "pending", "rejected"]:
    if value not in {"approved", "pending", "rejected"}:
        raise EstateFlowError(code="invalid_status", message="Invalid audience tag status.")
    return value  # type: ignore[return-value]


def _review_status(value: str) -> ReviewStatus:
    if value not in {"pending", "approved", "rejected", "merged"}:
        raise EstateFlowError(code="invalid_status", message="Invalid manual review status.")
    return value  # type: ignore[return-value]


def _listener_topology_response(status: ListenerTopologyStatus) -> ListenerTopologyStatusResponse:
    return ListenerTopologyStatusResponse(
        generated_at=status.generated_at.isoformat(),
        refresh_version=status.refresh_version,
        session_dir=status.session_dir,
        adapter_report=status.adapter_report,
        worker_snapshot=(
            ListenerSnapshotResponse(
                generated_at=status.worker_snapshot.generated_at.isoformat(),
                refresh_version=status.worker_snapshot.refresh_version,
                listener_account_key=status.worker_snapshot.listener_account_key,
                started=status.worker_snapshot.started,
                reason=status.worker_snapshot.reason,
                source_count=status.worker_snapshot.source_count,
                telegram_source_count=status.worker_snapshot.telegram_source_count,
                listener_count=status.worker_snapshot.listener_count,
                adapter_failures=list(status.worker_snapshot.adapter_failures),
                groups=[
                    ListenerTopologyGroupResponse(
                        adapter_name=group.adapter_name,
                        source_profile=group.source_profile,
                        source_count=group.source_count,
                        source_ids=list(group.source_ids),
                        source_identifiers=list(group.source_identifiers),
                        source_names=list(group.source_names),
                        runnable=group.runnable,
                        error=group.error,
                    )
                    for group in status.worker_snapshot.groups
                ],
            )
            if status.worker_snapshot is not None
            else None
        ),
        sources=[
            ListenerSourceStatusResponse(
                source_id=source.source_id,
                name=source.name,
                source_type=source.source_type,
                identifier=source.identifier,
                enabled=source.enabled,
                adapter_name=source.adapter_name,
                source_profile=source.source_profile,
                listener_account_key=source.listener_account_key,
                assigned_account_key=source.assigned_account_key,
                assignment_active=source.assignment_active,
                assignment_reason=source.assignment_reason,
                session_name_hint=source.session_name_hint,
                session_path_hint=source.session_path_hint,
            )
            for source in status.sources
        ],
        listener_accounts=[
            ListenerAccountStatusResponse(
                account_key=account.account_key,
                enabled=account.enabled,
                health_status=account.health_status,
                last_successful_event_at=(
                    account.last_successful_event_at.isoformat()
                    if account.last_successful_event_at is not None
                    else None
                ),
                flood_wait_count=account.flood_wait_count,
                assigned_source_count=account.assigned_source_count,
                session_name_hint=account.session_name_hint,
                session_path_hint=account.session_path_hint,
            )
            for account in status.accounts
        ],
        assignments=[
            ListenerAssignmentStatusResponse(
                source_id=assignment.source_id,
                account_key=assignment.account_key,
                assigned_at=assignment.assigned_at.isoformat(),
                active=assignment.active,
                released_at=assignment.released_at.isoformat()
                if assignment.released_at is not None
                else None,
                reason=assignment.reason,
            )
            for assignment in status.assignments
        ],
    )


def _telegram_session_status_response(
    session: TelegramSessionInspection,
) -> TelegramSessionStatusResponse:
    bound_sources = session.bound_sources
    return TelegramSessionStatusResponse(
        session_name=session.session_name,
        session_path=session.session_path,
        session_file_path=session.session_file_path,
        file_exists=bool(session.file_exists),
        file_size_bytes=session.file_size_bytes,
        authorized=session.authorized,
        auth_state_present=bool(session.auth_state_present),
        requires_password=bool(session.requires_password),
        updated_at=session.updated_at,
        status=session.status,
        error=session.error,
        bound_sources=[
            TelegramSessionSourceBindingResponse(
                source_id=binding.source_id,
                source_type=binding.source_type,
                identifier=binding.identifier,
                name=binding.name,
                source_profile=binding.source_profile,
                enabled=bool(binding.enabled),
                session_name=binding.session_name,
            )
            for binding in bound_sources
        ],
    )
