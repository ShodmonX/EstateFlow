from __future__ import annotations

import logging
from uuid import uuid4

from estateflow.bot.callbacks import CallbackPayload
from estateflow.bot.menu import (
    BotScreen,
    coming_soon_screen,
    help_screen,
    main_menu,
    mini_app_screen,
    saved_filters_empty_state,
    search_empty_state,
)
from estateflow.bot.nlp_search import NlpSearchBotFlow
from estateflow.bot.saved_filters import SavedFilterBotController
from estateflow.bot.search_wizard import SearchWizard
from estateflow.bot.source_suggestions import SourceSuggestionBotFlow
from estateflow.services.analytics import AnalyticsEventName, AnalyticsRecorder, safe_record_event
from estateflow.services.notifications import parse_notification_action_callback
from estateflow.services.referrals import ReferralService
from estateflow.services.release_controls import ReleaseControlService
from estateflow.services.saved_filters import SavedFilterService
from estateflow.services.source_suggestions import SourceSuggestionService
from estateflow.services.users import UserService

logger = logging.getLogger(__name__)


class BotController:
    def __init__(
        self,
        *,
        user_service: UserService,
        search_wizard: SearchWizard | None = None,
        nlp_search_flow: NlpSearchBotFlow | None = None,
        saved_filter_service: SavedFilterService | None = None,
        referral_service: ReferralService | None = None,
        source_suggestion_service: SourceSuggestionService | None = None,
        analytics_recorder: AnalyticsRecorder | None = None,
        release_controls: ReleaseControlService | None = None,
        mini_app_url: str | None = None,
        bot_username: str | None = None,
    ) -> None:
        self._user_service = user_service
        self._search_wizard = search_wizard
        self._nlp_search_flow = nlp_search_flow
        self._saved_filter_service = saved_filter_service
        self._referral_service = referral_service
        self._analytics_recorder = analytics_recorder
        self._release_controls = release_controls
        self._mini_app_url = mini_app_url
        self._bot_username = bot_username
        self._saved_filter_controller = (
            SavedFilterBotController(saved_filter_service)
            if saved_filter_service is not None
            else None
        )
        self._source_suggestion_flow = (
            SourceSuggestionBotFlow(source_suggestion_service)
            if source_suggestion_service is not None
            else None
        )

    async def start(self, *, user_id: int, start_parameter: str | None = None) -> BotScreen:
        blocked = await self._beta_access_blocked(user_id=user_id)
        if blocked is not None:
            return blocked
        if self._referral_service is not None:
            await self._referral_service.register_start(
                user_id=user_id,
                start_parameter=start_parameter,
            )
        else:
            await self._user_service.register_or_touch(telegram_user_id=user_id)
        return main_menu(user_id=user_id, mini_app_url=self._mini_app_url)

    async def handle_menu_callback(self, *, user_id: int, data: str) -> BotScreen:
        blocked = await self._beta_access_blocked(user_id=user_id)
        if blocked is not None:
            return blocked
        if data.startswith("n:v1:"):
            return await self._handle_notification_callback(user_id=user_id, data=data)
        payload = CallbackPayload.unpack(data, expected_owner_id=user_id)
        if payload.namespace == "wizard":
            if self._search_wizard is None:
                return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
            return await self._search_wizard.handle_callback(
                user_id=user_id,
                action=payload.action,
                value=payload.value,
            )
        if payload.namespace == "nlp":
            if self._nlp_search_flow is None or not await self._feature_enabled("nlp"):
                return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
            return await self._nlp_search_flow.handle_callback(
                user_id=user_id,
                action=payload.action,
                value=payload.value,
            )
        if payload.namespace == "filter":
            if self._saved_filter_controller is None:
                return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
            return await self._handle_filter_callback(
                user_id=user_id,
                action=payload.action,
                value=payload.value,
            )
        if payload.namespace == "source":
            if self._source_suggestion_flow is None:
                return coming_soon_screen()
            if payload.action == "start":
                return self._source_suggestion_flow.start(user_id=user_id)
            if payload.action == "cancel":
                return self._source_suggestion_flow.cancel(user_id=user_id)
            raise ValueError("Unsupported source suggestion callback action.")
        if payload.namespace == "referral":
            if payload.action != "link" or self._bot_username is None:
                return BotScreen(
                    text="Referral havolasi hozircha sozlanmagan.",
                    buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
                )
            user = await self._user_service.register_or_touch(telegram_user_id=user_id)
            link = f"https://t.me/{self._bot_username.lstrip('@')}?start={user.referral_code}"
            return BotScreen(
                text=(
                    "Do'stingizga shu havolani yuboring:\n\n"
                    f"{link}\n\n"
                    "Do'stingiz kamida bitta qidiruv yoki filter ishlatsa, "
                    "referral faol hisoblanadi."
                ),
                buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
            )
        if payload.namespace != "menu":
            raise ValueError("Unsupported callback namespace.")
        if payload.action == "back":
            return main_menu(user_id=user_id, mini_app_url=self._mini_app_url)
        if payload.action == "cancel":
            return BotScreen(
                text="Bekor qilindi.",
                buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
            )
        if payload.action == "search":
            return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
        if payload.action == "filters":
            if self._saved_filter_service is None:
                return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
            filters = await self._saved_filter_service.list_for_user(user_id=user_id)
            if not filters:
                return saved_filters_empty_state(user_id=user_id)
            text = "\n".join(
                f"{index}. {item.name} ({'yoqilgan' if item.enabled else 'o‘chirilgan'})"
                for index, item in enumerate(filters, start=1)
            )
            return BotScreen(
                text=text,
                buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
            )
            if self._saved_filter_service is None:
                return saved_filters_empty_state(user_id=user_id)
            filters = await self._saved_filter_service.list_for_user(user_id=user_id)
            if not filters:
                return saved_filters_empty_state(user_id=user_id)
            text = "\n".join(
                f"{index}. {item.name} ({'yoqilgan' if item.enabled else 'o‘chirilgan'})"
                for index, item in enumerate(filters, start=1)
            )
            return BotScreen(
                text=text,
                buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
            )
        if payload.action == "soon":
            return coming_soon_screen()
        if payload.action == "mini_app":
            return coming_soon_screen()
        if payload.action == "help":
            return help_screen()
        raise ValueError("Unsupported callback action.")

    async def handle_text(self, *, user_id: int, text: str) -> BotScreen:
        blocked = await self._beta_access_blocked(user_id=user_id)
        if blocked is not None:
            return blocked
        if (
            self._source_suggestion_flow is not None
            and self._source_suggestion_flow.has_pending_input(user_id=user_id)
        ):
            return await self._source_suggestion_flow.handle_text(user_id=user_id, text=text)
        if self._saved_filter_controller is not None:
            screen = await self._saved_filter_controller.handle_name_text(
                user_id=user_id,
                text=text,
            )
            if screen is not None:
                return screen
        if (
            self._nlp_search_flow is not None
            and await self._feature_enabled("nlp")
            and self._nlp_search_flow.has_pending_input(user_id=user_id)
        ):
            return await self._nlp_search_flow.handle_text(user_id=user_id, text=text)
        if (
            self._source_suggestion_flow is not None
            and self._source_suggestion_flow.has_pending_input(user_id=user_id)
        ):
            return await self._source_suggestion_flow.handle_text(user_id=user_id, text=text)
        if self._search_wizard is None:
            return mini_app_screen(user_id=user_id, mini_app_url=self._mini_app_url)
        return self._search_wizard.handle_text(user_id=user_id, text=text)

    async def cancel(self, *, user_id: int) -> BotScreen:
        if self._search_wizard is not None:
            self._search_wizard.cancel(user_id=user_id)
        if self._nlp_search_flow is not None:
            self._nlp_search_flow.cancel(user_id=user_id)
        if self._source_suggestion_flow is not None:
            self._source_suggestion_flow.cancel(user_id=user_id)
        return BotScreen(
            text="Bekor qilindi.",
            buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
        )

    async def back(self, *, user_id: int) -> BotScreen:
        if self._search_wizard is not None:
            try:
                return self._search_wizard.back(user_id=user_id)
            except ValueError:
                pass
        return main_menu(user_id=user_id, mini_app_url=self._mini_app_url)

    async def _handle_filter_callback(
        self,
        *,
        user_id: int,
        action: str,
        value: str,
    ) -> BotScreen:
        if self._saved_filter_controller is None:
            return saved_filters_empty_state(user_id=user_id)
        if action == "list":
            return await self._saved_filter_controller.list_screen(user_id=user_id)
        if action == "view":
            return await self._saved_filter_controller.view_screen(
                user_id=user_id,
                filter_id=value,
            )
        if action == "save":
            if self._search_wizard is None:
                return search_empty_state(user_id=user_id)
            return self._saved_filter_controller.begin_save(
                user_id=user_id,
                criteria=self._search_wizard.current_criteria(user_id=user_id),
            )
        if action == "update":
            if self._search_wizard is None:
                return search_empty_state(user_id=user_id)
            filter_id = self._saved_filter_controller.pending_edit_filter_id(user_id=user_id)
            if filter_id is None:
                return BotScreen(
                    text="Yangilash uchun avval saqlangan filtrdan Tahrirlashni tanlang.",
                    buttons=(),
                )
            return self._saved_filter_controller.begin_update(
                user_id=user_id,
                filter_id=filter_id,
                criteria=self._search_wizard.current_criteria(user_id=user_id),
            )
        if action == "run":
            if self._search_wizard is None:
                return search_empty_state(user_id=user_id)
            criteria = await self._saved_filter_controller.get_criteria(
                user_id=user_id,
                filter_id=value,
            )
            return await self._search_wizard.run_criteria(user_id=user_id, criteria=criteria)
        if action == "edit":
            if self._search_wizard is None:
                return search_empty_state(user_id=user_id)
            criteria = await self._saved_filter_controller.get_criteria(
                user_id=user_id,
                filter_id=value,
            )
            self._saved_filter_controller.begin_edit(user_id=user_id, filter_id=value)
            screen = await self._search_wizard.run_criteria(user_id=user_id, criteria=criteria)
            return BotScreen(
                text=(
                    "Mavjud kriteriyalar bo'yicha natija. "
                    "Yangi kriteriya uchun qidiruvni reset qiling, "
                    "yakunda 'Filtrni yangilash' bosing.\n\n"
                    f"{screen.text}"
                )[:3900],
                buttons=screen.buttons,
            )
        if action == "enable":
            return await self._saved_filter_controller.set_enabled_screen(
                user_id=user_id,
                filter_id=value,
                enabled=True,
            )
        if action == "disable":
            return await self._saved_filter_controller.set_enabled_screen(
                user_id=user_id,
                filter_id=value,
                enabled=False,
            )
        if action == "delete_confirm":
            return await self._saved_filter_controller.delete_confirm_screen(
                user_id=user_id,
                filter_id=value,
            )
        if action == "delete":
            return await self._saved_filter_controller.delete_screen(
                user_id=user_id,
                filter_id=value,
            )
        if action == "cancel":
            return self._saved_filter_controller.cancel_pending(user_id=user_id)
        raise ValueError("Unsupported filter action.")

    async def _handle_notification_callback(self, *, user_id: int, data: str) -> BotScreen:
        action, notification_id = parse_notification_action_callback(data)
        event_name: AnalyticsEventName
        if action == "open":
            event_name = "notification_action_open"
        elif action == "search":
            event_name = "notification_action_search"
        else:
            event_name = "notification_action_read"
        await safe_record_event(
            self._analytics_recorder,
            event_name=event_name,
            idempotency_key=f"notification_action:{notification_id}:{action}:{user_id}",
            user_id=user_id,
            subject_id=notification_id,
        )
        if action == "read":
            return BotScreen(
                text="Bildirishnoma o'qildi.",
                buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
            )
        if action == "search":
            return main_menu(user_id=user_id, mini_app_url=self._mini_app_url)
        return BotScreen(
            text="E'lon tafsilotlari ochildi.",
            buttons=main_menu(user_id=user_id, mini_app_url=self._mini_app_url).buttons,
        )

    async def _feature_enabled(self, name: str) -> bool:
        if self._release_controls is None:
            return True
        flags = await self._release_controls.flags()
        return bool(getattr(flags, name))

    async def _beta_access_blocked(self, *, user_id: int) -> BotScreen | None:
        if self._release_controls is None:
            return None
        decision = await self._release_controls.beta_access(user_id=user_id)
        if decision.allowed:
            return None
        return BotScreen(
            text=(
                "Beta hozir yopiq rejimda. Kirish faqat admin tasdiqlagan "
                "test cohort uchun yoqilgan."
            ),
            buttons=(),
        )

    async def user_safe_error(self, exc: Exception) -> tuple[str, str]:
        correlation_id = uuid4().hex[:8]
        logger.exception("Bot handler failed correlation_id=%s", correlation_id, exc_info=exc)
        return (
            "Xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.",
            correlation_id,
        )
