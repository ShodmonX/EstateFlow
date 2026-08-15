from __future__ import annotations

from dataclasses import dataclass

from estateflow.bot.callbacks import callback


@dataclass(frozen=True)
class MenuButton:
    text: str
    callback_data: str
    enabled: bool = True
    web_app_url: str | None = None


@dataclass(frozen=True)
class BotScreen:
    text: str
    buttons: tuple[MenuButton, ...]


def main_menu(*, user_id: int, mini_app_url: str | None = None) -> BotScreen:
    return BotScreen(
        text="EstateFlow. E'lonlarni Mini App orqali qulay ko'ring.",
        buttons=(
            MenuButton(
                "Mini App'ni ochish",
                callback("menu", "mini_app", owner_id=user_id),
                enabled=mini_app_url is not None,
                web_app_url=mini_app_url,
            ),
            MenuButton(
                "Bildirishnomalar",
                callback("menu", "mini_app", owner_id=user_id, value="notifications"),
                enabled=mini_app_url is not None,
                web_app_url=mini_app_url,
            ),
            MenuButton(
                "Do'stlarni taklif qilish",
                callback("referral", "link", owner_id=user_id),
            ),
            MenuButton(
                "Kanal taklif qilish",
                callback("source", "start", owner_id=user_id),
            ),
            MenuButton("Yordam", callback("menu", "help", owner_id=user_id)),
        ),
    )


def coming_soon_screen() -> BotScreen:
    return BotScreen(text="Bu bo'lim tez orada ishga tushadi.", buttons=())


def mini_app_screen(*, user_id: int, mini_app_url: str | None = None) -> BotScreen:
    return BotScreen(
        text="Qidiruv va e'lonlar Mini App ichida ishlaydi.",
        buttons=(
            MenuButton(
                "Mini App'ni ochish",
                callback("menu", "mini_app", owner_id=user_id),
                enabled=mini_app_url is not None,
                web_app_url=mini_app_url,
            ),
        ),
    )


def help_screen() -> BotScreen:
    return BotScreen(
        text=(
            "Qidiruv va saqlangan filterlar Mini App ichida ishlaydi. "
            "canonical e'lonlar va bildirishnomalar shu tizimda ko'rsatiladi."
        ),
        buttons=(),
    )


def search_empty_state(*, user_id: int) -> BotScreen:
    return BotScreen(
        text="Qidiruv Mini App ichida ochiladi.",
        buttons=(MenuButton("Ortga", callback("menu", "back", owner_id=user_id)),),
    )


def saved_filters_empty_state(*, user_id: int) -> BotScreen:
    return BotScreen(
        text="Hali saqlangan filtr yo'q.",
        buttons=(MenuButton("Ortga", callback("menu", "back", owner_id=user_id)),),
    )
