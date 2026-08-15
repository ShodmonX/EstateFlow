from __future__ import annotations

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

from estateflow.services.notifications import (
    TelegramNotificationClient,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramSendResult,
    TelegramTransientError,
    TelegramUserBlockedError,
)


class AiogramTelegramNotificationClient(TelegramNotificationClient):
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
