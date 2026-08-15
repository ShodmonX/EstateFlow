from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.application.runtime import build_runtime
from estateflow.bot.app import create_bot_dispatcher

logger = logging.getLogger(__name__)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings)
    if settings.telegram_bot_token is None:
        logger.error(
            "Telegram bot token is missing",
            extra={
                "event": "bot.token_missing",
                "service_name": settings.service_name,
            },
        )
        raise ValueError("TELEGRAM_BOT_TOKEN is required to run the Telegram bot.")
    logger.info(
        "Bot starting",
        extra={
            "event": "bot.starting",
            "service_name": settings.service_name,
        },
    )
    runtime = build_runtime(settings, role="bot")
    try:
        dispatcher = create_bot_dispatcher(settings, controller=runtime.bot_controller)
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        async with bot:
            await dispatcher.start_polling(bot)
    finally:
        await runtime.aclose()
        logger.info(
            "Bot stopped",
            extra={
                "event": "bot.stopped",
                "service_name": settings.service_name,
            },
        )


def main() -> None:
    asyncio.run(_run())
