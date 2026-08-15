from __future__ import annotations

from aiogram import Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from estateflow.application.core.config import Settings
from estateflow.bot.controller import BotController
from estateflow.bot.menu import BotScreen


def create_bot_dispatcher(settings: Settings, *, controller: BotController) -> Dispatcher:
    if settings.telegram_bot_token is None:
        raise ValueError("TELEGRAM_BOT_TOKEN is required to run the user bot.")
    dispatcher = Dispatcher()
    dispatcher.include_router(create_bot_router(controller=controller))
    return dispatcher


def create_bot_router(*, controller: BotController) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user is None:
            return
        screen = await controller.start(
            user_id=message.from_user.id,
            start_parameter=_start_parameter(message.text),
        )
        await _answer(message, screen)

    @router.message(Command("cancel"))
    async def cancel(message: Message) -> None:
        if message.from_user is None:
            return
        screen = await controller.cancel(user_id=message.from_user.id)
        await _answer(message, screen)

    @router.message(Command("back"))
    async def back(message: Message) -> None:
        if message.from_user is None:
            return
        screen = await controller.back(user_id=message.from_user.id)
        await _answer(message, screen)

    @router.message()
    async def text(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        try:
            screen = await controller.handle_text(user_id=message.from_user.id, text=message.text)
        except ValueError as exc:
            screen = BotScreen(text=str(exc), buttons=())
        await _answer(message, screen)

    @router.callback_query()
    async def callback(callback_query: CallbackQuery) -> None:
        user_id = callback_query.from_user.id
        data = callback_query.data or ""
        try:
            screen = await controller.handle_menu_callback(user_id=user_id, data=data)
        except PermissionError:
            await callback_query.answer(
                "Bu tugma boshqa foydalanuvchiga tegishli.",
                show_alert=True,
            )
            return
        except (TimeoutError, ValueError):
            await callback_query.answer("Bu tugma eskirgan. Menyuni qayta oching.", show_alert=True)
            return
        if callback_query.message is not None:
            await callback_query.message.answer(screen.text, reply_markup=_markup(screen))
        await callback_query.answer()

    @router.errors()
    async def errors(event: ErrorEvent) -> None:
        message, correlation_id = await controller.user_safe_error(event.exception)
        update = event.update
        if update.message is not None:
            await update.message.answer(f"{message}\nID: {correlation_id}")
        elif update.callback_query is not None:
            await update.callback_query.answer(message, show_alert=True)

    return router


async def _answer(message: Message, screen: BotScreen) -> None:
    await message.answer(screen.text, reply_markup=_markup(screen))


def _markup(screen: BotScreen) -> InlineKeyboardMarkup | None:
    if not screen.buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button.text if button.enabled else f"{button.text} (tez orada)",
                    **(
                        {"web_app": WebAppInfo(url=button.web_app_url)}
                        if button.enabled and button.web_app_url
                        else {"callback_data": button.callback_data}
                    ),
                )
            ]
            for button in screen.buttons
        ]
    )


def _start_parameter(text: str | None) -> str | None:
    if text is None:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0] != "/start":
        return None
    return parts[1]
