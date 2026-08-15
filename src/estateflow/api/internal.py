from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, Request

from estateflow.api.admin import SourceSuggestionResponse, SourceSuggestionSubmitRequest
from estateflow.application.core.exceptions import EstateFlowError
from estateflow.services.source_suggestions import (
    SourceSuggestion,
    SourceSuggestionService,
)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/source-suggestions", response_model=SourceSuggestionResponse)
async def submit_source_suggestion(
    request: Request,
    body: SourceSuggestionSubmitRequest,
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> SourceSuggestion:
    _internal_auth(request=request, token=x_internal_token)
    service = _source_suggestion_service(request)
    try:
        result = await service.submit(
            user_id=x_telegram_user_id,
            source_identifier=body.source_identifier,
            source_type=body.source_type,
        )
    except ValueError as exc:
        raise EstateFlowError(code="invalid_source_suggestion", message=str(exc)) from exc
    return result.suggestion


def _internal_auth(request: Request, token: str | None) -> None:
    settings = request.app.state.settings
    expected = (
        settings.source_suggestion_ingress_token.get_secret_value()
        if settings.source_suggestion_ingress_token
        else None
    )
    if not expected:
        raise EstateFlowError(
            code="source_suggestion_ingress_unavailable",
            message="Source suggestion ingress is not configured.",
            status_code=503,
        )
    if token is None or not secrets.compare_digest(token, expected):
        raise EstateFlowError(
            code="source_suggestion_ingress_unauthorized",
            message="Source suggestion ingress authorization failed.",
            status_code=403,
        )


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
