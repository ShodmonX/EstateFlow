from __future__ import annotations

import asyncio
import logging

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging

logger = logging.getLogger(__name__)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "Worker starting",
        extra={
            "event": "worker.starting",
            "service_name": settings.service_name,
        },
    )

    try:
        from estateflow.application.worker_stages import run_worker_stage

        await run_worker_stage(settings)
    finally:
        logger.info(
            "Worker stopped",
            extra={
                "event": "worker.stopped",
                "service_name": settings.service_name,
            },
        )


def main() -> None:
    asyncio.run(_run())
