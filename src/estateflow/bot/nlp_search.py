from __future__ import annotations

from dataclasses import dataclass

from estateflow.bot.callbacks import callback
from estateflow.bot.menu import BotScreen, MenuButton
from estateflow.bot.search_wizard import SearchWizard
from estateflow.services.nlp_search import (
    NlpSearchExtraction,
    NlpSearchExtractionFailedError,
    NlpSearchExtractor,
    validate_nlp_query_text,
)


@dataclass(frozen=True)
class NlpSearchState:
    owner_id: int
    extraction: NlpSearchExtraction | None = None


class NlpSearchBotFlow:
    def __init__(
        self,
        *,
        extractor: NlpSearchExtractor,
        search_wizard: SearchWizard,
    ) -> None:
        self._extractor = extractor
        self._search_wizard = search_wizard
        self._states: dict[int, NlpSearchState] = {}

    def start(self, *, user_id: int) -> BotScreen:
        self._states[user_id] = NlpSearchState(owner_id=user_id)
        return BotScreen(
            text=(
                "Qidiruvni erkin matnda yozing. Masalan: "
                "Yunusobod 2 xona 600$ gacha yosh oila uchun"
            ),
            buttons=_control_buttons(user_id),
        )

    def cancel(self, *, user_id: int) -> BotScreen:
        self._states.pop(user_id, None)
        return BotScreen(text="Erkin qidiruv bekor qilindi.", buttons=())

    def has_pending_input(self, *, user_id: int) -> bool:
        state = self._states.get(user_id)
        return state is not None and state.extraction is None

    async def handle_text(self, *, user_id: int, text: str) -> BotScreen:
        state = self._require_state(user_id)
        try:
            validate_nlp_query_text(text)
        except ValueError as exc:
            return BotScreen(text=str(exc), buttons=_control_buttons(user_id))
        try:
            extraction = await self._extractor.extract(text)
        except NlpSearchExtractionFailedError:
            return _fallback_screen(user_id=user_id)
        updated = NlpSearchState(owner_id=state.owner_id, extraction=extraction)
        self._states[user_id] = updated
        return _summary_screen(user_id=user_id, extraction=extraction)

    async def handle_callback(self, *, user_id: int, action: str, value: str) -> BotScreen:
        if action == "start":
            return self.start(user_id=user_id)
        if action == "cancel":
            return self.cancel(user_id=user_id)
        if action == "edit":
            self._states.pop(user_id, None)
            return self._search_wizard.start(user_id=user_id)
        if action == "confirm":
            state = self._require_state(user_id)
            if state.extraction is None:
                raise ValueError("NLP search criteria is not ready.")
            self._states.pop(user_id, None)
            return await self._search_wizard.run_criteria(
                user_id=user_id,
                criteria=state.extraction.criteria,
            )
        raise ValueError("Unsupported NLP search action.")

    def _require_state(self, user_id: int) -> NlpSearchState:
        state = self._states.get(user_id)
        if state is None:
            raise ValueError("NLP search state not found.")
        if state.owner_id != user_id:
            raise PermissionError("NLP search owner mismatch.")
        return state


def _summary_screen(*, user_id: int, extraction: NlpSearchExtraction) -> BotScreen:
    criteria = extraction.criteria
    lines = [
        "AI tushungan filterlar:",
        f"Tuman: {', '.join(criteria.selected_districts) or 'tanlanmagan'}",
        f"Xona: {criteria.rooms if criteria.rooms is not None else 'tanlanmagan'}",
        f"Byudjet: {criteria.max_price if criteria.max_price is not None else 'tanlanmagan'}",
        "Narx turi: " + ("kishi boshiga ham" if criteria.include_per_person else "faqat umumiy"),
        f"Remont: {criteria.renovation_level or 'tanlanmagan'}",
        f"Auditoriya: {criteria.audience_tag or 'tanlanmagan'}",
    ]
    if extraction.unapplied_conditions:
        lines.append("Qo'llanmagan shartlar: " + ", ".join(extraction.unapplied_conditions))
    return BotScreen(
        text="\n".join(lines),
        buttons=(
            MenuButton("Tasdiqlash", callback("nlp", "confirm", owner_id=user_id)),
            MenuButton("Wizardda tahrirlash", callback("nlp", "edit", owner_id=user_id)),
            MenuButton("Bekor qilish", callback("nlp", "cancel", owner_id=user_id)),
        ),
    )


def _fallback_screen(*, user_id: int) -> BotScreen:
    return BotScreen(
        text=(
            "Matnni ishonchli filterga aylantira olmadim. "
            "Qidiruvni bosqichma-bosqich wizard orqali davom ettirishingiz mumkin."
        ),
        buttons=(
            MenuButton("Wizardni ochish", callback("nlp", "edit", owner_id=user_id)),
            MenuButton("Bekor qilish", callback("nlp", "cancel", owner_id=user_id)),
        ),
    )


def _control_buttons(user_id: int) -> tuple[MenuButton, ...]:
    return (
        MenuButton("Wizardga o'tish", callback("nlp", "edit", owner_id=user_id)),
        MenuButton("Bekor qilish", callback("nlp", "cancel", owner_id=user_id)),
    )
