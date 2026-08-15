from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.application.runtime import (
    _AiogramTelegramNotificationClient,
    _notification_bot,
)
from estateflow.services.notifications import TelegramRateLimitError, TelegramUserBlockedError


class FakeBot:
    def __init__(self, outcome: object | None = None) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object | None]] = []
        self.closed = False

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: object | None = None,
    ) -> object:
        self.calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or SimpleNamespace(message_id=321)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_notification_client_wraps_markup_and_message_id() -> None:
    bot = FakeBot()
    client = _AiogramTelegramNotificationClient(bot)  # type: ignore[arg-type]

    result = await client.send_message(
        chat_id=123,
        text="hello",
        reply_markup={
            "inline_keyboard": [[{"text": "Open", "callback_data": "n:v1:open:1"}]],
        },
    )

    assert result.message_id == "321"
    assert bot.calls[0]["chat_id"] == 123
    assert isinstance(bot.calls[0]["reply_markup"], InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_notification_client_translates_telegram_exceptions() -> None:
    rate_limit_bot = FakeBot(
        TelegramRetryAfter(
            method=SendMessage(chat_id=123, text="hello"),
            message="Too Many Requests",
            retry_after=7,
        )
    )
    blocked_bot = FakeBot(
        TelegramForbiddenError(
            method=SendMessage(chat_id=123, text="hello"),
            message="bot was blocked by the user",
        )
    )

    rate_limit_client = _AiogramTelegramNotificationClient(rate_limit_bot)  # type: ignore[arg-type]
    blocked_client = _AiogramTelegramNotificationClient(blocked_bot)  # type: ignore[arg-type]

    with pytest.raises(TelegramRateLimitError):
        await rate_limit_client.send_message(chat_id=123, text="hello")

    with pytest.raises(TelegramUserBlockedError):
        await blocked_client.send_message(chat_id=123, text="hello")


def test_notification_bot_helper_respects_missing_token() -> None:
    assert _notification_bot(Settings(environment="test", telegram_bot_token=None)) is None


def test_notification_bot_helper_builds_bot_when_configured() -> None:
    settings = Settings(
        environment="test",
        telegram_bot_token=SecretStr("123456:ABCDEF"),
        feature_notifications_enabled=True,
    )

    bot = _notification_bot(settings)

    assert bot is not None
    assert bot.token == "123456:ABCDEF"
