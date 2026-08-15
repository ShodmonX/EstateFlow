from __future__ import annotations

from estateflow.bot.callbacks import callback
from estateflow.bot.menu import BotScreen, MenuButton
from estateflow.services.source_suggestions import SourceSuggestionService


class SourceSuggestionBotFlow:
    def __init__(self, service: SourceSuggestionService) -> None:
        self._service = service
        self._awaiting_input: set[int] = set()

    def start(self, *, user_id: int) -> BotScreen:
        self._awaiting_input.add(user_id)
        return BotScreen(
            text=(
                "Kanal yoki guruh username/linkini yuboring.\n"
                "Masalan: @estate_channel yoki https://t.me/estate_channel\n\n"
                "Manba avtomatik qo'shilmaydi. Adminlar faolligi va sifatini ko'rib chiqadi."
            ),
            buttons=(MenuButton("Bekor qilish", callback("source", "cancel", owner_id=user_id)),),
        )

    def has_pending_input(self, *, user_id: int) -> bool:
        return user_id in self._awaiting_input

    async def handle_text(self, *, user_id: int, text: str) -> BotScreen:
        if user_id not in self._awaiting_input:
            raise ValueError("Kanal taklif qilish oqimi boshlanmagan.")
        try:
            result = await self._service.submit(user_id=user_id, source_identifier=text)
        except ValueError as exc:
            return BotScreen(
                text=f"{exc}\n\nIltimos, @username yoki https://t.me/... link yuboring.",
                buttons=(
                    MenuButton("Bekor qilish", callback("source", "cancel", owner_id=user_id)),
                ),
            )
        self._awaiting_input.discard(user_id)
        if result.created:
            return BotScreen(
                text=(
                    "Taklif qabul qilindi va pending review holatiga qo'yildi.\n"
                    f"Manba: {result.suggestion.source_identifier}\n\n"
                    "Bu kanal avtomatik qo'shilmaydi. Admin tasdiqlasa, manba monitoringga "
                    "topshiriladi va sizga 1 haftalik Premium beriladi."
                ),
                buttons=(),
            )
        return BotScreen(
            text=(
                "Bu manba bo'yicha taklif allaqachon mavjud.\n"
                f"Manba: {result.suggestion.source_identifier}\n"
                f"Holat: {result.suggestion.status}\n\n"
                "Adminlar uni ko'rib chiqadi; duplicate taklif qayta qo'shilmadi."
            ),
            buttons=(),
        )

    def cancel(self, *, user_id: int) -> BotScreen:
        self._awaiting_input.discard(user_id)
        return BotScreen(text="Kanal taklif qilish bekor qilindi.", buttons=())
