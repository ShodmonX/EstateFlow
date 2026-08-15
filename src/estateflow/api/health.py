from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    checker = request.app.state.health_checker
    dependency_statuses = await checker.check()
    is_ready = all(item.status == "ok" for item in dependency_statuses)
    payload: dict[str, Any] = {
        "status": "ok" if is_ready else "error",
        "dependencies": [
            {
                "name": item.name,
                "status": item.status,
                "error": item.error,
            }
            for item in dependency_statuses
        ],
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )
