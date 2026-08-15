from __future__ import annotations

from dataclasses import dataclass

from estateflow.bot.callbacks import callback
from estateflow.bot.menu import BotScreen, MenuButton
from estateflow.services.saved_filters import SavedFilterService, UserFilter
from estateflow.services.search import SearchCriteria


@dataclass(frozen=True)
class PendingFilterWrite:
    criteria: SearchCriteria
    filter_id: str | None = None


class SavedFilterBotController:
    def __init__(self, service: SavedFilterService) -> None:
        self._service = service
        self._pending_names: dict[int, PendingFilterWrite] = {}
        self._pending_edit_filter_ids: dict[int, str] = {}

    async def list_screen(self, *, user_id: int) -> BotScreen:
        filters = await self._service.list_for_user(user_id=user_id)
        if not filters:
            return BotScreen(text="Hali saqlangan filtr yo'q.", buttons=())
        return BotScreen(
            text="Saqlangan filtrlar",
            buttons=tuple(
                MenuButton(
                    _filter_title(item),
                    callback("filter", "view", owner_id=user_id, value=item.filter_id),
                )
                for item in filters
            ),
        )

    async def view_screen(self, *, user_id: int, filter_id: str) -> BotScreen:
        item = await self._get(user_id=user_id, filter_id=filter_id)
        return BotScreen(
            text=_filter_details(item),
            buttons=(
                MenuButton(
                    "Qayta qidirish",
                    callback("filter", "run", owner_id=user_id, value=filter_id),
                ),
                MenuButton(
                    "Tahrirlash",
                    callback("filter", "edit", owner_id=user_id, value=filter_id),
                ),
                MenuButton(
                    "O'chirish" if item.enabled else "Yoqish",
                    callback(
                        "filter",
                        "disable" if item.enabled else "enable",
                        owner_id=user_id,
                        value=filter_id,
                    ),
                ),
                MenuButton(
                    "Butunlay o'chirish",
                    callback("filter", "delete_confirm", owner_id=user_id, value=filter_id),
                ),
                MenuButton("Ro'yxat", callback("filter", "list", owner_id=user_id)),
            ),
        )

    def begin_save(self, *, user_id: int, criteria: SearchCriteria) -> BotScreen:
        self._pending_names[user_id] = PendingFilterWrite(criteria=criteria)
        return BotScreen(
            text="Filtr nomini yozing. Masalan: Yunusobod 2 xona",
            buttons=(MenuButton("Bekor qilish", callback("filter", "cancel", owner_id=user_id)),),
        )

    def begin_update(
        self,
        *,
        user_id: int,
        filter_id: str,
        criteria: SearchCriteria,
    ) -> BotScreen:
        self._pending_names[user_id] = PendingFilterWrite(criteria=criteria, filter_id=filter_id)
        return BotScreen(
            text="Yangilangan filtr nomini yozing yoki eski nom uchun /skip yuboring.",
            buttons=(MenuButton("Bekor qilish", callback("filter", "cancel", owner_id=user_id)),),
        )

    def begin_edit(self, *, user_id: int, filter_id: str) -> None:
        self._pending_edit_filter_ids[user_id] = filter_id

    def pending_edit_filter_id(self, *, user_id: int) -> str | None:
        return self._pending_edit_filter_ids.get(user_id)

    async def handle_name_text(self, *, user_id: int, text: str) -> BotScreen | None:
        pending = self._pending_names.get(user_id)
        if pending is None:
            return None
        self._pending_names.pop(user_id, None)
        name = None if text.strip() == "/skip" else text
        if pending.filter_id is None:
            if name is None:
                name = _default_name(pending.criteria)
            created = await self._service.create(
                user_id=user_id,
                name=name,
                criteria=pending.criteria,
            )
            return await self.view_screen(user_id=user_id, filter_id=created.filter_id)
        updated = await self._service.update(
            user_id=user_id,
            filter_id=pending.filter_id,
            name=name,
            criteria=pending.criteria,
        )
        self._pending_edit_filter_ids.pop(user_id, None)
        return await self.view_screen(user_id=user_id, filter_id=updated.filter_id)

    async def set_enabled_screen(
        self,
        *,
        user_id: int,
        filter_id: str,
        enabled: bool,
    ) -> BotScreen:
        updated = await self._service.set_enabled(
            user_id=user_id,
            filter_id=filter_id,
            enabled=enabled,
        )
        return await self.view_screen(user_id=user_id, filter_id=updated.filter_id)

    async def delete_confirm_screen(self, *, user_id: int, filter_id: str) -> BotScreen:
        item = await self._get(user_id=user_id, filter_id=filter_id)
        return BotScreen(
            text=f"'{item.name}' filtrini butunlay o'chirasizmi?",
            buttons=(
                MenuButton(
                    "Ha, o'chirish",
                    callback("filter", "delete", owner_id=user_id, value=filter_id),
                ),
                MenuButton("Yo'q", callback("filter", "view", owner_id=user_id, value=filter_id)),
            ),
        )

    async def delete_screen(self, *, user_id: int, filter_id: str) -> BotScreen:
        deleted = await self._service.delete(user_id=user_id, filter_id=filter_id)
        if not deleted:
            raise PermissionError("Saved filter not found or not owned by user.")
        return BotScreen(
            text="Filtr o'chirildi.",
            buttons=(MenuButton("Ro'yxat", callback("filter", "list", owner_id=user_id)),),
        )

    def cancel_pending(self, *, user_id: int) -> BotScreen:
        self._pending_names.pop(user_id, None)
        self._pending_edit_filter_ids.pop(user_id, None)
        return BotScreen(text="Saqlash bekor qilindi.", buttons=())

    async def get_criteria(self, *, user_id: int, filter_id: str) -> SearchCriteria:
        item = await self._get(user_id=user_id, filter_id=filter_id)
        return item.criteria

    async def _get(self, *, user_id: int, filter_id: str) -> UserFilter:
        item = await self._service.get_for_user(user_id=user_id, filter_id=filter_id)
        if item is None:
            raise PermissionError("Saved filter not found or not owned by user.")
        return item


def _filter_title(item: UserFilter) -> str:
    status = "on" if item.enabled else "off"
    return f"{item.name} ({status})"


def _filter_details(item: UserFilter) -> str:
    criteria = item.criteria
    return "\n".join(
        [
            f"Nomi: {item.name}",
            f"Holat: {'yoqilgan' if item.enabled else 'o‘chirilgan'}",
            f"Tuman: {criteria.district or 'skip'}",
            f"Xona: {criteria.rooms if criteria.rooms is not None else 'skip'}",
            f"Byudjet: {criteria.max_price if criteria.max_price is not None else 'skip'}",
            f"Kishiga narx: {'ha' if criteria.include_per_person else 'yoq'}",
            f"Remont: {criteria.renovation_level or 'skip'}",
            f"Auditoriya: {criteria.audience_tag or 'skip'}",
        ]
    )


def _default_name(criteria: SearchCriteria) -> str:
    district = criteria.district or "Barcha tumanlar"
    rooms = f"{criteria.rooms} xona" if criteria.rooms is not None else "xona skip"
    price = f"{criteria.max_price:g}$" if criteria.max_price is not None else "byudjet skip"
    return f"{district} {rooms} {price}"
