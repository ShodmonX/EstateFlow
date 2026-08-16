from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from estateflow.bot.callbacks import callback
from estateflow.bot.menu import BotScreen, MenuButton
from estateflow.bot.rendering import render_search_page
from estateflow.services.analytics import AnalyticsRecorder, safe_record_event
from estateflow.services.extraction import AUDIENCE_TAGS, RenovationLevel
from estateflow.services.search import SearchCriteria, SearchResult, SearchService

WizardStep = Literal["district", "rooms", "budget", "basis", "renovation", "audience", "done"]

KNOWN_DISTRICTS: dict[str, str] = {
    "yunusobod": "Yunusobod",
    "yunusabad": "Yunusobod",
    "chilonzor": "Chilonzor",
    "chilanazar": "Chilonzor",
    "sergeli": "Sergeli",
    "mirzo ulugbek": "Mirzo Ulugbek",
    "mirzo ulug'bek": "Mirzo Ulugbek",
    "yakkasaroy": "Yakkasaroy",
    "shayxontohur": "Shayxontohur",
    "olmazor": "Olmazor",
    "uchtepa": "Uchtepa",
    "mirobod": "Mirobod",
    "yashnobod": "Yashnobod",
    "bektemir": "Bektemir",
}
RENOVATION_LEVELS: tuple[RenovationLevel, ...] = ("none", "basic", "good", "euro", "luxury")
PAGE_SIZE = 3


class SearchActivationRecorder(Protocol):
    async def record_qualifying_action(
        self,
        *,
        user_id: int,
        action: Literal["search"],
        action_id: str,
    ) -> object: ...


@dataclass(frozen=True)
class SearchWizardState:
    owner_id: int
    step: WizardStep = "district"
    district: str | None = None
    districts: tuple[str, ...] = ()
    rooms: int | None = None
    max_price: Decimal | None = None
    include_per_person: bool = False
    renovation_level: RenovationLevel | None = None
    audience_tag: str | None = None

    def criteria(self, *, offset: int = 0) -> SearchCriteria:
        return SearchCriteria(
            district=self.district,
            districts=list(self.districts),
            rooms=self.rooms,
            max_price=self.max_price,
            include_per_person=self.include_per_person,
            renovation_level=self.renovation_level,
            audience_tag=self.audience_tag,
            limit=PAGE_SIZE,
            offset=offset,
        )


class SearchWizard:
    def __init__(
        self,
        search_service: SearchService,
        *,
        activation_recorder: SearchActivationRecorder | None = None,
        analytics_recorder: AnalyticsRecorder | None = None,
    ) -> None:
        self._search_service = search_service
        self._activation_recorder = activation_recorder
        self._analytics_recorder = analytics_recorder
        self._states: dict[int, SearchWizardState] = {}

    def start(self, *, user_id: int) -> BotScreen:
        state = SearchWizardState(owner_id=user_id)
        self._states[user_id] = state
        return _prompt(state)

    def cancel(self, *, user_id: int) -> BotScreen:
        self._states.pop(user_id, None)
        return BotScreen(text="Qidiruv bekor qilindi.", buttons=())

    def back(self, *, user_id: int) -> BotScreen:
        state = self._require_state(user_id)
        previous = _previous_step(state.step)
        updated = replace(state, step=previous)
        self._states[user_id] = updated
        return _prompt(updated)

    def skip(self, *, user_id: int) -> BotScreen:
        state = self._require_state(user_id)
        updated = _clear_step_value(state)
        updated = replace(updated, step=_next_step(state.step))
        self._states[user_id] = updated
        return _prompt(updated)

    def update(
        self,
        *,
        user_id: int,
        district: str | None = None,
        rooms: int | None = None,
        max_price: Decimal | str | None = None,
        include_per_person: bool | None = None,
        renovation_level: RenovationLevel | None = None,
        audience_tag: str | None = None,
    ) -> SearchWizardState:
        state = self._states.get(user_id)
        if state is None:
            state = SearchWizardState(owner_id=user_id)
        parsed_price = parse_price(max_price) if isinstance(max_price, str) else max_price
        selected_districts = state.districts
        selected_district = state.district
        if district is not None:
            selected_districts = normalize_districts(district)
            selected_district = selected_districts[0]
        updated = SearchWizardState(
            owner_id=state.owner_id,
            step=state.step,
            district=selected_district,
            districts=selected_districts,
            rooms=state.rooms if rooms is None else validate_rooms(rooms),
            max_price=state.max_price if max_price is None else parsed_price,
            include_per_person=state.include_per_person
            if include_per_person is None
            else include_per_person,
            renovation_level=state.renovation_level
            if renovation_level is None
            else renovation_level,
            audience_tag=state.audience_tag
            if audience_tag is None
            else validate_audience(audience_tag),
        )
        updated.criteria()
        self._states[user_id] = updated
        return updated

    def handle_text(self, *, user_id: int, text: str) -> BotScreen:
        state = self._require_state(user_id)
        if state.step == "district":
            districts = normalize_districts(text)
            updated = replace(state, district=districts[0], districts=districts, step="rooms")
        elif state.step == "rooms":
            updated = replace(state, rooms=validate_rooms_text(text), step="budget")
        elif state.step == "budget":
            updated = replace(state, max_price=parse_price(text), step="basis")
        else:
            return BotScreen(
                text="Bu qadamda tugmalardan foydalaning.",
                buttons=_control_buttons(user_id),
            )
        self._states[user_id] = updated
        return _prompt(updated)

    async def handle_callback(self, *, user_id: int, action: str, value: str) -> BotScreen:
        if action == "start":
            return self.start(user_id=user_id)
        if action == "cancel":
            return self.cancel(user_id=user_id)
        if action == "back":
            return self.back(user_id=user_id)
        if action == "skip":
            state = self._require_state(user_id)
            if state.step == "done":
                return self.start(user_id=user_id)
            return self.skip(user_id=user_id)
        if action == "reset":
            return self.start(user_id=user_id)
        if action == "basis_total":
            state = self._expect_step(user_id, "basis")
            state = replace(
                state,
                include_per_person=False,
                step="renovation",
            )
            self._states[user_id] = state
            return _prompt(state)
        if action == "basis_any":
            state = self._expect_step(user_id, "basis")
            state = replace(
                state,
                include_per_person=True,
                step="renovation",
            )
            self._states[user_id] = state
            return _prompt(state)
        if action == "renovation":
            self._expect_step(user_id, "renovation")
            level = None if value == "skip" else validate_renovation(value)
            state = replace(self._require_state(user_id), renovation_level=level, step="audience")
            self._states[user_id] = state
            return _prompt(state)
        if action == "audience":
            self._expect_step(user_id, "audience")
            tag = None if value == "skip" else validate_audience(value)
            state = replace(self._require_state(user_id), audience_tag=tag, step="done")
            self._states[user_id] = state
            return await self.run(user_id=user_id, offset=0)
        if action == "page":
            self._expect_step(user_id, "done")
            return await self.run(user_id=user_id, offset=validate_offset(value))
        raise ValueError("Unsupported wizard action.")

    async def run(self, *, user_id: int, offset: int = 0) -> BotScreen:
        state = self._require_state(user_id)
        if offset == 0:
            await self._record_search_activation(user_id=user_id, criteria=state.criteria(offset=0))
        result = await self._search_service.search(state.criteria(offset=offset))
        if offset == 0:
            await safe_record_event(
                self._analytics_recorder,
                event_name="search_completed",
                idempotency_key=f"search_completed:user:{user_id}:{state.criteria(offset=0).model_dump_json()}",
                user_id=user_id,
                metadata={"result_count": str(result.metadata.returned)},
            )
        return result_screen(user_id=user_id, result=result)

    async def run_criteria(
        self,
        *,
        user_id: int,
        criteria: SearchCriteria,
    ) -> BotScreen:
        state = SearchWizardState(
            owner_id=user_id,
            step="done",
            district=criteria.district,
            districts=criteria.selected_districts,
            rooms=criteria.rooms,
            max_price=criteria.max_price,
            include_per_person=criteria.include_per_person,
            renovation_level=criteria.renovation_level,
            audience_tag=criteria.audience_tag,
        )
        self._states[user_id] = state
        await self._record_search_activation(user_id=user_id, criteria=state.criteria(offset=0))
        result = await self._search_service.search(state.criteria(offset=0))
        await safe_record_event(
            self._analytics_recorder,
            event_name="search_completed",
            idempotency_key=f"search_completed:user:{user_id}:{state.criteria(offset=0).model_dump_json()}",
            user_id=user_id,
            metadata={"result_count": str(result.metadata.returned)},
        )
        return result_screen(user_id=user_id, result=result)

    def current_criteria(self, *, user_id: int) -> SearchCriteria:
        return self._require_state(user_id).criteria(offset=0)

    def _require_state(self, user_id: int) -> SearchWizardState:
        state = self._states.get(user_id)
        if state is None:
            raise ValueError("Search wizard state not found.")
        if state.owner_id != user_id:
            raise PermissionError("Search wizard owner mismatch.")
        return state

    def _expect_step(self, user_id: int, step: WizardStep) -> SearchWizardState:
        state = self._require_state(user_id)
        if state.step != step:
            raise ValueError("Unexpected wizard step.")
        return state

    async def _record_search_activation(
        self,
        *,
        user_id: int,
        criteria: SearchCriteria,
    ) -> None:
        if self._activation_recorder is None:
            return
        await self._activation_recorder.record_qualifying_action(
            user_id=user_id,
            action="search",
            action_id=f"user:{user_id}:{criteria.model_dump_json()}",
        )


def result_screen(*, user_id: int, result: SearchResult) -> BotScreen:
    buttons: list[MenuButton] = []
    offset = result.metadata.offset
    limit = result.metadata.limit
    total = result.metadata.total
    if offset > 0:
        buttons.append(
            MenuButton(
                "Oldingi",
                callback("wizard", "page", owner_id=user_id, value=str(max(0, offset - limit))),
            )
        )
    if total is None or offset + result.metadata.returned < total:
        buttons.append(
            MenuButton(
                "Keyingi",
                callback("wizard", "page", owner_id=user_id, value=str(offset + limit)),
            )
        )
    buttons.extend(
        [
            MenuButton("Filtrni saqlash", callback("filter", "save", owner_id=user_id)),
            MenuButton("Filtrni yangilash", callback("filter", "update", owner_id=user_id)),
            MenuButton("Filtrlarni reset", callback("wizard", "reset", owner_id=user_id)),
            MenuButton("Bekor qilish", callback("wizard", "cancel", owner_id=user_id)),
        ]
    )
    return BotScreen(text=render_search_page(result), buttons=tuple(buttons))


def parse_price(value: str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    compact = re.sub(r"\s+", "", value.casefold())
    normalized = re.sub(r"[^\d.,]", "", compact).replace(",", ".")
    if not normalized:
        raise ValueError("Price is required.")
    try:
        price = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Invalid price format.") from exc
    if price < 0 or price > Decimal("100000000000"):
        raise ValueError("Price is outside allowed range.")
    return price


def normalize_district(value: str) -> str:
    key = " ".join(value.strip().casefold().split())
    if key not in KNOWN_DISTRICTS:
        raise ValueError("Unknown district.")
    return KNOWN_DISTRICTS[key]


def normalize_districts(value: str) -> tuple[str, ...]:
    parts = re.split(r"\s+(?:va|yoki|or|или)\s+|[,;/]+", value.strip(), flags=re.IGNORECASE)
    districts: list[str] = []
    for part in parts:
        clean = normalize_district(part)
        if clean.casefold() not in {district.casefold() for district in districts}:
            districts.append(clean)
    if not districts:
        raise ValueError("At least one district is required.")
    return tuple(districts)


def validate_rooms(value: int) -> int:
    if value < 0 or value > 20:
        raise ValueError("Rooms value is outside allowed range.")
    return value


def validate_rooms_text(value: str) -> int:
    match = re.search(r"\d+", value)
    if match is None:
        raise ValueError("Rooms value is required.")
    return validate_rooms(int(match.group(0)))


def validate_renovation(value: str) -> RenovationLevel:
    if value not in RENOVATION_LEVELS:
        raise ValueError("Unknown renovation level.")
    return value


def validate_audience(value: str) -> str:
    tag = value.strip().casefold()
    if tag not in AUDIENCE_TAGS:
        raise ValueError("Unknown audience tag.")
    return tag


def validate_offset(value: str) -> int:
    offset = int(value)
    if offset < 0 or offset > 10_000:
        raise ValueError("Invalid page offset.")
    return offset


def _prompt(state: SearchWizardState) -> BotScreen:
    user_id = state.owner_id
    if state.step == "district":
        return BotScreen(
            text="Tumanni yoki tumanlarni yozing. Masalan: Yunusobod yoki Chilonzor",
            buttons=_control_buttons(user_id),
        )
    if state.step == "rooms":
        return BotScreen(
            text="Xonalar sonini yozing. Masalan: 2",
            buttons=_control_buttons(user_id),
        )
    if state.step == "budget":
        return BotScreen(
            text="Oylik maksimal byudjetni yozing. Masalan: 500$",
            buttons=_control_buttons(user_id),
        )
    if state.step == "basis":
        return BotScreen(
            text="Narx turi:",
            buttons=(
                MenuButton("Faqat umumiy", callback("wizard", "basis_total", owner_id=user_id)),
                MenuButton("Kishiga narx ham", callback("wizard", "basis_any", owner_id=user_id)),
                *_control_buttons(user_id),
            ),
        )
    if state.step == "renovation":
        return BotScreen(
            text="Remont darajasini tanlang.",
            buttons=tuple(
                MenuButton(level, callback("wizard", "renovation", owner_id=user_id, value=level))
                for level in RENOVATION_LEVELS
            )
            + (
                MenuButton(
                    "Skip",
                    callback("wizard", "renovation", owner_id=user_id, value="skip"),
                ),
            )
            + _control_buttons(user_id),
        )
    if state.step == "audience":
        return BotScreen(
            text="Auditoriya tegini tanlang.",
            buttons=tuple(
                MenuButton(tag, callback("wizard", "audience", owner_id=user_id, value=tag))
                for tag in sorted(AUDIENCE_TAGS)
            )
            + (MenuButton("Skip", callback("wizard", "audience", owner_id=user_id, value="skip")),)
            + _control_buttons(user_id),
        )
    return BotScreen(text="Qidiruv tayyor.", buttons=())


def _control_buttons(user_id: int) -> tuple[MenuButton, ...]:
    return (
        MenuButton("Skip", callback("wizard", "skip", owner_id=user_id)),
        MenuButton("Ortga", callback("wizard", "back", owner_id=user_id)),
        MenuButton("Bekor qilish", callback("wizard", "cancel", owner_id=user_id)),
    )


def _next_step(step: WizardStep) -> WizardStep:
    order: tuple[WizardStep, ...] = (
        "district",
        "rooms",
        "budget",
        "basis",
        "renovation",
        "audience",
        "done",
    )
    return order[min(order.index(step) + 1, len(order) - 1)]


def _previous_step(step: WizardStep) -> WizardStep:
    order: tuple[WizardStep, ...] = (
        "district",
        "rooms",
        "budget",
        "basis",
        "renovation",
        "audience",
        "done",
    )
    return order[max(order.index(step) - 1, 0)]


def _clear_step_value(state: SearchWizardState) -> SearchWizardState:
    if state.step == "district":
        return replace(state, district=None, districts=())
    if state.step == "rooms":
        return replace(state, rooms=None)
    if state.step == "budget":
        return replace(state, max_price=None)
    if state.step == "renovation":
        return replace(state, renovation_level=None)
    if state.step == "audience":
        return replace(state, audience_tag=None)
    return state
