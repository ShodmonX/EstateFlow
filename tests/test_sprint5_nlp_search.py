from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from estateflow.bot.callbacks import callback
from estateflow.bot.controller import BotController
from estateflow.bot.nlp_search import NlpSearchBotFlow
from estateflow.bot.search_wizard import SearchWizard
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.ai_client import LLMMalformedResponseError, LLMRequest, LLMResponse
from estateflow.services.extraction import CanonicalListing
from estateflow.services.nlp_search import NlpSearchExtractor
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService
from estateflow.services.users import UserService


class FakeNlpLlm:
    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(model="fake", content=outcome, confidence=0.95), []


@pytest.mark.asyncio
async def test_nlp_search_extracts_uzbek_text_to_typed_search_criteria() -> None:
    llm = FakeNlpLlm(
        [
            {
                "monthly_budget": "600",
                "price_basis_preference": "total",
                "districts": ["Yunusobod"],
                "rooms": 2,
                "renovation_level": "good",
                "audience_tag": "yosh oila",
                "unapplied_conditions": [],
                "confidence": 0.9,
            }
        ]
    )
    extractor = NlpSearchExtractor(llm_client=llm)

    extraction = await extractor.extract("Yunusobod 2 xona 600$ gacha yosh oila uchun")

    assert extraction.criteria == SearchCriteria(
        district="Yunusobod",
        rooms=2,
        max_price=Decimal("600"),
        renovation_level="good",
        audience_tag="family",
    )
    assert "Never create SQL" in llm.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_nlp_search_extracts_russian_text_and_per_person_preference() -> None:
    extractor = NlpSearchExtractor(
        llm_client=FakeNlpLlm(
            [
                {
                    "monthly_budget": "450",
                    "price_basis_preference": "include_per_person",
                    "districts": ["Чиланзар"],
                    "rooms": 1,
                    "renovation_level": "euro",
                    "audience_tag": "студенты",
                    "unapplied_conditions": [],
                    "confidence": 0.8,
                }
            ]
        )
    )

    extraction = await extractor.extract("Чиланзар 1 комнатная до 450, можно для студентов")

    assert extraction.criteria.district == "Chilonzor"
    assert extraction.criteria.include_per_person is True
    assert extraction.criteria.audience_tag == "students"


@pytest.mark.asyncio
async def test_nlp_search_tracks_mixed_unmodelled_conditions_without_applying_them() -> None:
    extractor = NlpSearchExtractor(
        llm_client=FakeNlpLlm(
            [
                {
                    "monthly_budget": "800",
                    "price_basis_preference": "total",
                    "districts": ["Yunusabad", "Chilonzor"],
                    "rooms": 3,
                    "renovation_level": None,
                    "audience_tag": None,
                    "unapplied_conditions": ["metroga yaqin"],
                    "confidence": 0.7,
                }
            ]
        )
    )

    extraction = await extractor.extract("Yunusabad yoki Chilonzor, 3 xona, metroga yaqin")

    assert extraction.criteria.district == "Yunusobod"
    assert extraction.criteria.selected_districts == ("Yunusobod", "Chilonzor")
    assert extraction.criteria.rooms == 3
    assert "metroga yaqin" in extraction.unapplied_conditions


@pytest.mark.asyncio
async def test_nlp_bot_rejects_empty_and_long_input_with_clear_feedback() -> None:
    flow = _flow(FakeNlpLlm([]))
    flow.start(user_id=1)

    empty = await flow.handle_text(user_id=1, text="   ")
    long = await flow.handle_text(user_id=1, text="x" * 141)

    assert "matnini yozing" in empty.text
    assert "140" in long.text


@pytest.mark.asyncio
async def test_nlp_bot_invalid_model_json_offers_wizard_fallback() -> None:
    flow = _flow(FakeNlpLlm([{"monthly_budget": "500", "unexpected": True}]))
    flow.start(user_id=1)

    screen = await flow.handle_text(user_id=1, text="Yunusobod 2 xona")

    assert "wizard" in screen.text.casefold()
    assert any(button.text == "Wizardni ochish" for button in screen.buttons)


@pytest.mark.asyncio
async def test_nlp_bot_model_failure_does_not_stop_search_and_edit_opens_wizard() -> None:
    flow = _flow(FakeNlpLlm([LLMMalformedResponseError("bad json")]))
    flow.start(user_id=1)

    fallback = await flow.handle_text(user_id=1, text="Yunusobod 2 xona")
    edit = await flow.handle_callback(user_id=1, action="edit", value="")

    assert "wizard" in fallback.text.casefold()
    assert "Tumanni" in edit.text


@pytest.mark.asyncio
async def test_nlp_bot_summary_confirm_runs_existing_search_service() -> None:
    announcement = _announcement("a1")
    search_wizard = SearchWizard(SearchService(InMemorySearchRepository([announcement])))
    flow = NlpSearchBotFlow(
        extractor=NlpSearchExtractor(
            llm_client=FakeNlpLlm(
                [
                    {
                        "monthly_budget": "600",
                        "price_basis_preference": "total",
                        "districts": ["Yunusobod"],
                        "rooms": 2,
                        "renovation_level": "good",
                        "audience_tag": "young family",
                        "unapplied_conditions": ["metroga yaqin"],
                        "confidence": 0.9,
                    }
                ]
            )
        ),
        search_wizard=search_wizard,
    )
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        search_wizard=search_wizard,
        nlp_search_flow=flow,
    )
    await controller.start(user_id=22)

    start = await controller.handle_menu_callback(
        user_id=22,
        data=callback("nlp", "start", owner_id=22),
    )
    summary = await controller.handle_text(user_id=22, text="Yunusobod 2 xona yosh oila")
    result = await controller.handle_menu_callback(
        user_id=22,
        data=callback("nlp", "confirm", owner_id=22),
    )

    assert "erkin matnda" in start.text
    assert "Auditoriya: family" in summary.text
    assert "metroga yaqin" in summary.text
    assert "Yunusobod listing" in result.text
    assert search_wizard.current_criteria(user_id=22).audience_tag == "family"


def _flow(llm: FakeNlpLlm) -> NlpSearchBotFlow:
    return NlpSearchBotFlow(
        extractor=NlpSearchExtractor(llm_client=llm),
        search_wizard=SearchWizard(SearchService(InMemorySearchRepository())),
    )


def _canonical() -> CanonicalListing:
    return CanonicalListing(
        prompt_version="test",
        price=Decimal("500"),
        currency="USD",
        price_period="monthly",
        price_basis="total",
        price_normalized_monthly=Decimal("500"),
        listing_type="rent",
        rooms=2,
        area_sqm=None,
        floor=None,
        total_floors=None,
        district="Yunusobod",
        address=None,
        phone_numbers=[],
        owner_type="unknown",
        renovation_level="good",
        renovation_source="vision",
        furniture=None,
        description="Yunusobod listing",
        audience_tags=["family"],
        audience_excluded_tags=[],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(announcement_id: str) -> StructuredAnnouncement:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=now,
        canonical=_canonical(),
        parent_id=None,
        status="active",
        source_count=1,
        source_url=f"https://example.test/{announcement_id}",
        created_at=now,
        updated_at=now,
    )
