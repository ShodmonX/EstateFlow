from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.bot.app import create_bot_dispatcher
from estateflow.bot.callbacks import CallbackPayload, callback
from estateflow.bot.controller import BotController
from estateflow.bot.menu import main_menu
from estateflow.bot.search_wizard import SearchWizard, normalize_districts, parse_price
from estateflow.repositories.saved_filters import InMemorySavedFilterRepository
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.extraction import CanonicalListing
from estateflow.services.media_storage import StoredMedia
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.saved_filters import SavedFilterService
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService
from estateflow.services.users import UserService


def test_bot_main_menu_has_safe_disabled_future_items() -> None:
    screen = main_menu(user_id=42)

    assert screen.buttons[0].text == "Mini App'ni ochish"
    assert any(button.enabled is False for button in screen.buttons)
    assert all(
        CallbackPayload.unpack(button.callback_data, expected_owner_id=42).owner_id == 42
        for button in screen.buttons
    )


def test_callback_payload_is_versioned_and_owner_bound() -> None:
    packed = callback("menu", "search", owner_id=10, value="20")

    payload = CallbackPayload.unpack(packed, expected_owner_id=10)

    assert payload.namespace == "menu"
    assert payload.action == "search"
    assert payload.value == "20"
    with pytest.raises(PermissionError):
        CallbackPayload.unpack(packed, expected_owner_id=11)
    with pytest.raises(ValueError):
        CallbackPayload.unpack("broken")
    expired = CallbackPayload(
        namespace="menu",
        action="help",
        owner_id=10,
        issued_at=int((datetime.now(UTC) - timedelta(days=2)).timestamp()),
    ).pack()
    with pytest.raises(TimeoutError):
        CallbackPayload.unpack(expired, expected_owner_id=10)


def test_create_bot_dispatcher_requires_settings_token() -> None:
    controller = BotController(user_service=UserService(InMemoryUserRepository()))

    with pytest.raises(ValueError):
        create_bot_dispatcher(
            Settings.model_construct(environment="test", telegram_bot_token=None),
            controller=controller,
        )

    dispatcher = create_bot_dispatcher(
        Settings(environment="test", telegram_bot_token=SecretStr("123456:ABCDEF")),
        controller=controller,
    )

    assert dispatcher is not None


@pytest.mark.asyncio
async def test_bot_start_upserts_minimal_user_and_returns_main_menu() -> None:
    user_repo = InMemoryUserRepository()
    controller = BotController(user_service=UserService(user_repo))

    screen = await controller.start(user_id=1001)

    assert "Mini App" in screen.text
    assert user_repo.users[1001].referral_code == "ef_1001"
    assert user_repo.users[1001].status == "active"


@pytest.mark.asyncio
async def test_bot_menu_navigation_and_safe_future_items() -> None:
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        search_wizard=SearchWizard(SearchService(InMemorySearchRepository())),
    )
    await controller.start(user_id=5)

    search_screen = await controller.handle_menu_callback(
        user_id=5,
        data=callback("wizard", "start", owner_id=5),
    )
    soon_screen = await controller.handle_menu_callback(
        user_id=5,
        data=callback("menu", "soon", owner_id=5, value="notifications"),
    )
    help_screen = await controller.handle_menu_callback(
        user_id=5,
        data=callback("menu", "help", owner_id=5),
    )

    assert "Tumanni" in search_screen.text
    assert "tez orada" in soon_screen.text
    assert "canonical" in help_screen.text
    with pytest.raises(PermissionError):
        await controller.handle_menu_callback(
            user_id=6,
            data=callback("menu", "help", owner_id=5),
        )


@pytest.mark.asyncio
async def test_bot_error_message_is_user_safe_and_has_correlation_id() -> None:
    controller = BotController(user_service=UserService(InMemoryUserRepository()))

    message, correlation_id = await controller.user_safe_error(RuntimeError("secret-token"))

    assert "secret-token" not in message
    assert "Xatolik" in message
    assert len(correlation_id) == 8


def test_search_wizard_validates_price_and_builds_criteria() -> None:
    wizard = SearchWizard(SearchService(InMemorySearchRepository()))

    wizard.start(user_id=7)
    state = wizard.update(
        user_id=7,
        district="Chilonzor",
        rooms=1,
        max_price="450$",
        audience_tag="students",
    )

    assert state.criteria() == SearchCriteria(
        district="Chilonzor",
        rooms=1,
        max_price=Decimal("450"),
        audience_tag="students",
        limit=3,
    )
    assert parse_price("1 200,50 usd") == Decimal("1200.50")
    with pytest.raises(ValueError):
        parse_price("narx yo'q")


def test_search_wizard_accepts_multiple_districts() -> None:
    assert normalize_districts("Olmazor yoki Chilonzor") == ("Olmazor", "Chilonzor")


@pytest.mark.asyncio
async def test_search_wizard_happy_path_renders_duplicate_free_canonical_results() -> None:
    repo = InMemorySearchRepository(
        [
            _announcement(
                "accepted",
                canonical=_canonical(audience_tags=["family"]),
                media=(_media(),),
            ),
            _announcement(
                "excluded",
                canonical=_canonical(audience_tags=["family"], audience_excluded_tags=["family"]),
            ),
            _announcement(
                "child",
                canonical=_canonical(audience_tags=["family"]),
                parent_id="accepted",
            ),
        ]
    )
    wizard = SearchWizard(SearchService(repo))

    screen = wizard.start(user_id=77)
    assert "Tumanni" in screen.text
    assert "Xonalar" in wizard.handle_text(user_id=77, text="Yunusobod").text
    assert "byudjet" in wizard.handle_text(user_id=77, text="2 xona").text
    assert "Narx turi" in wizard.handle_text(user_id=77, text="600$").text
    assert (
        "Remont" in (await wizard.handle_callback(user_id=77, action="basis_total", value="")).text
    )
    assert (
        "Auditoriya"
        in (await wizard.handle_callback(user_id=77, action="renovation", value="good")).text
    )

    result = await wizard.handle_callback(user_id=77, action="audience", value="family")

    assert "accepted" in result.text or "Yunusobod listing" in result.text
    assert "Davr/asos: monthly/total" in result.text
    assert "Rasm: bor" in result.text
    assert "excluded" not in result.text
    assert "child" not in result.text


def test_search_wizard_back_cancel_and_invalid_inputs_are_user_scoped() -> None:
    wizard = SearchWizard(SearchService(InMemorySearchRepository()))
    wizard.start(user_id=1)
    wizard.start(user_id=2)

    assert "Xonalar" in wizard.handle_text(user_id=1, text="Chilonzor").text
    assert "Tumanni" in wizard.back(user_id=1).text
    assert "Xonalar" in wizard.handle_text(user_id=2, text="Yunusobod").text
    assert "bekor" in wizard.cancel(user_id=1).text
    with pytest.raises(ValueError):
        wizard.handle_text(user_id=1, text="2")
    with pytest.raises(ValueError):
        wizard.handle_text(user_id=2, text="Unknown district")


@pytest.mark.asyncio
async def test_search_wizard_no_results_and_pagination_callbacks_are_owner_bound() -> None:
    repo = InMemorySearchRepository(
        [_announcement(f"item-{index}", created_at_offset=index) for index in range(5)]
    )
    wizard = SearchWizard(SearchService(repo))
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        search_wizard=wizard,
    )
    await controller.start(user_id=9)
    wizard.start(user_id=9)
    wizard.handle_text(user_id=9, text="Sergeli")
    wizard.handle_text(user_id=9, text="5")
    wizard.handle_text(user_id=9, text="10")
    await wizard.handle_callback(user_id=9, action="basis_total", value="")
    await wizard.handle_callback(user_id=9, action="renovation", value="skip")
    no_result = await wizard.handle_callback(user_id=9, action="audience", value="skip")

    assert "Mos e'lon topilmadi" in no_result.text
    assert any(
        button.callback_data for button in no_result.buttons if "reset" in button.callback_data
    )

    wizard.start(user_id=9)
    wizard.skip(user_id=9)
    wizard.skip(user_id=9)
    wizard.skip(user_id=9)
    await wizard.handle_callback(user_id=9, action="basis_total", value="")
    await wizard.handle_callback(user_id=9, action="renovation", value="skip")
    paged = await wizard.handle_callback(user_id=9, action="audience", value="skip")
    next_button = next(button for button in paged.buttons if button.text == "Keyingi")
    with pytest.raises(PermissionError):
        await controller.handle_menu_callback(user_id=10, data=next_button.callback_data)
    next_page = await controller.handle_menu_callback(user_id=9, data=next_button.callback_data)
    assert next_page.text


@pytest.mark.asyncio
async def test_saved_filter_bot_end_to_end_crud_from_wizard_result() -> None:
    search_repo = InMemorySearchRepository([_announcement("a1")])
    saved_repo = InMemorySavedFilterRepository()
    saved_service = SavedFilterService(saved_repo)
    wizard = SearchWizard(SearchService(search_repo))
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        search_wizard=wizard,
        saved_filter_service=saved_service,
    )
    user_id = 333

    await controller.start(user_id=user_id)
    wizard.start(user_id=user_id)
    wizard.handle_text(user_id=user_id, text="Yunusobod")
    wizard.handle_text(user_id=user_id, text="2")
    wizard.handle_text(user_id=user_id, text="600")
    await wizard.handle_callback(user_id=user_id, action="basis_total", value="")
    await wizard.handle_callback(user_id=user_id, action="renovation", value="good")
    result = await wizard.handle_callback(user_id=user_id, action="audience", value="skip")
    save_button = next(button for button in result.buttons if button.text == "Filtrni saqlash")

    ask_name = await controller.handle_menu_callback(
        user_id=user_id,
        data=save_button.callback_data,
    )
    saved = await controller.handle_text(user_id=user_id, text="Uy qidiruv")
    filters = await saved_service.list_for_user(user_id=user_id)
    filter_id = filters[0].filter_id

    assert "Filtr nomini" in ask_name.text
    assert "Uy qidiruv" in saved.text
    assert filters[0].criteria == wizard.current_criteria(user_id=user_id)

    listed = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "list", owner_id=user_id),
    )
    viewed = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "view", owner_id=user_id, value=filter_id),
    )
    disabled = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "disable", owner_id=user_id, value=filter_id),
    )
    enabled = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "enable", owner_id=user_id, value=filter_id),
    )

    assert "Uy qidiruv" in listed.buttons[0].text
    assert "Byudjet: 600" in viewed.text
    assert "o‘chirilgan" in disabled.text
    assert "yoqilgan" in enabled.text

    await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "edit", owner_id=user_id, value=filter_id),
    )
    wizard.start(user_id=user_id)
    wizard.handle_text(user_id=user_id, text="Chilonzor")
    wizard.handle_text(user_id=user_id, text="1")
    wizard.handle_text(user_id=user_id, text="450")
    await wizard.handle_callback(user_id=user_id, action="basis_total", value="")
    await wizard.handle_callback(user_id=user_id, action="renovation", value="skip")
    await wizard.handle_callback(user_id=user_id, action="audience", value="skip")
    update_ask = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "update", owner_id=user_id),
    )
    updated = await controller.handle_text(user_id=user_id, text="/skip")
    run_result = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "run", owner_id=user_id, value=filter_id),
    )

    assert "Yangilangan filtr" in update_ask.text
    assert "Chilonzor" in updated.text
    assert run_result.text
    with pytest.raises(PermissionError):
        await controller.handle_menu_callback(
            user_id=444,
            data=callback("filter", "view", owner_id=444, value=filter_id),
        )

    confirm = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "delete_confirm", owner_id=user_id, value=filter_id),
    )
    deleted = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("filter", "delete", owner_id=user_id, value=filter_id),
    )

    assert "butunlay" in confirm.text
    assert "o'chirildi" in deleted.text
    assert await saved_service.list_for_user(user_id=user_id) == []


@pytest.mark.asyncio
async def test_saved_filters_crud_and_ownership() -> None:
    service = SavedFilterService(InMemorySavedFilterRepository(), max_filters_per_user=2)
    criteria = SearchCriteria(district="Yunusobod", rooms=2)

    created = await service.create(user_id=1, name="  Yunusobod 2xona ", criteria=criteria)
    assert created.name == "Yunusobod 2xona"
    assert created.criteria == criteria
    assert await service.get_for_user(user_id=2, filter_id=created.filter_id) is None

    updated = await service.update(
        user_id=1,
        filter_id=created.filter_id,
        enabled=False,
        criteria=SearchCriteria(max_price=Decimal("600")),
    )

    assert updated.enabled is False
    assert updated.criteria.max_price == Decimal("600")
    assert (await service.set_enabled(user_id=1, filter_id=created.filter_id, enabled=True)).enabled
    assert len(await service.list_for_user(user_id=1)) == 1
    assert await service.delete(user_id=2, filter_id=created.filter_id) is False
    assert await service.delete(user_id=1, filter_id=created.filter_id) is True


@pytest.mark.asyncio
async def test_saved_filters_validate_name_limit_and_criteria() -> None:
    service = SavedFilterService(InMemorySavedFilterRepository(), max_filters_per_user=1)
    await service.create(user_id=1, name="Valid", criteria=SearchCriteria())

    with pytest.raises(ValueError):
        await service.create(user_id=1, name="", criteria=SearchCriteria())
    with pytest.raises(ValueError):
        await service.create(user_id=1, name="Second", criteria=SearchCriteria())
    assert SearchCriteria(audience_tag="young_family").audience_tag == "family"


def _canonical(
    *,
    audience_tags: list[str] | None = None,
    audience_excluded_tags: list[str] | None = None,
) -> CanonicalListing:
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
        audience_tags=audience_tags or [],
        audience_excluded_tags=audience_excluded_tags or [],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(
    announcement_id: str,
    *,
    canonical: CanonicalListing | None = None,
    parent_id: str | None = None,
    media: tuple[StoredMedia, ...] = (),
    created_at_offset: int = 0,
) -> StructuredAnnouncement:
    created = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(seconds=created_at_offset)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=created,
        canonical=canonical or _canonical(),
        media=media,
        parent_id=parent_id,
        status="active",
        source_count=2,
        source_url=f"https://example.test/{announcement_id}",
        created_at=created,
        updated_at=created,
    )


def _media() -> StoredMedia:
    return StoredMedia(
        media_id="m1",
        storage_url="https://cdn.test/m1.jpg",
        object_key="announcements/m1.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        phash="0" * 16,
        content_sha256="a" * 64,
    )
