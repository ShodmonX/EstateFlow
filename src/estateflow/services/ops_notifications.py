from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from aiohttp import ClientSession
from pydantic import SecretStr

from estateflow.application.core.correlation import get_correlation_id
from estateflow.application.core.redaction import redact_text

logger = logging.getLogger(__name__)


def _field(reason: str, key: str, default: str = "noma'lum") -> str:
    match = re.search(rf"\b{re.escape(key)}=([^\s]+)", reason)
    return match.group(1) if match else default


def _readable_status(value: str) -> str:
    return {
        "success": "muvaffaqiyatli",
        "low_confidence": "ishonch darajasi past",
        "failed": "xatolik bilan tugadi",
        "high_confidence_duplicate": "aniq dublikat",
        "proceed_to_ai": "AI tekshiruviga yuborildi",
        "needs_reviewable_signal": "qo‘lda tekshiruv kerak",
        "manual_review": "qo‘lda tekshiruvga yuborildi",
        "auto_rejected": "avtomatik rad etildi",
        "online": "ishlayapti",
        "reconnecting": "qayta ulanmoqda",
        "flood_wait": "Telegram vaqtincha chekladi",
        "banned": "avtorizatsiya bloklangan",
        "missing": "topilmadi",
    }.get(value, value.replace("_", " "))


def humanize_ops_reason(reason: str) -> str:
    """Turn internal event codes into an operator-friendly Uzbek message."""
    if reason.startswith("llm_attempt "):
        model = _field(reason, "model")
        status = _readable_status(_field(reason, "status"))
        stage = _field(reason, "stage")
        latency = _field(reason, "latency_ms")
        details = f"AI modeli {model} {status} javob berdi."
        if stage != "noma'lum":
            details += f" Zaxira bosqichi: {stage}."
        if latency != "noma'lum":
            details += f" Javob vaqti: {latency} ms."
        error_type = _field(reason, "error_type", "none")
        if error_type != "none":
            details += f" Xatolik turi: {error_type}."
        return details

    if reason.startswith("pre_ai_dedup "):
        decision = _readable_status(_field(reason, "decision"))
        source = _field(reason, "source")
        signals = _field(reason, "signal_count")
        return (
            f"Telegram xabari dublikat tekshiruvdan o‘tdi. Qaror: {decision}. "
            f"Manba: {source}. Moslik belgilar soni: {signals}."
        )

    if reason.startswith("ai_processing succeeded "):
        return (
            f"E’lon AI tomonidan muvaffaqiyatli qayta ishlandi. "
            f"Manba: {_field(reason, 'source')}. "
            f"Saqlangan rasmlar soni: {_field(reason, 'media_count', '0')}."
        )

    if reason.startswith("ai_processing auto_rejected "):
        return (
            f"E’lon avtomatik rad etildi. Manba: {_field(reason, 'source')}. "
            f"Sabab: {_readable_status(_field(reason, 'reason'))}."
        )

    if reason.startswith("ai_processing manual_review "):
        return (
            f"E’lon qo‘lda tekshiruvga yuborildi. Manba: {_field(reason, 'source')}. "
            f"Sabab: {_readable_status(_field(reason, 'reason'))}."
        )

    if reason.startswith("ai_processing storage_failure "):
        retryable = _field(reason, "retryable")
        action = "qayta uriniladi" if retryable == "True" else "qo‘lda tekshirish kerak"
        return (
            f"E’lon rasmlarini storage'ga saqlashda muammo yuz berdi. "
            f"Manba: {_field(reason, 'source')}. Keyingi harakat: {action}."
        )

    if reason.startswith("ai_processing failed "):
        return (
            f"E’lonni AI qayta ishlay olmadi. Manba: {_field(reason, 'source')}. "
            "Barcha AI modellari ishlamagan yoki xatolik qaytargan."
        )

    if reason.startswith("notification delivery bulk failure"):
        return (
            f"Bir nechta foydalanuvchiga notification yuborilmadi. "
            f"Muammoli yuborishlar soni: {_field(reason, 'count', 'noma’lum')}."
        )

    if reason.startswith("notification filter match failed"):
        return (
            "Saqlangan filtrlarni e’lonlar bilan solishtirishda xatolik yuz berdi. "
            f"Xatolik turi: {reason.rsplit(':', 1)[-1].strip()}."
        )

    if reason.startswith("Listener account "):
        account = reason.split()[2] if len(reason.split()) > 2 else "noma’lum"
        if "hit FloodWait" in reason:
            seconds = reason.rsplit("for", 1)[-1].strip()
            return (
                f"Telegram listener {account} vaqtincha cheklovga tushdi. "
                f"Taxminiy kutish vaqti: {seconds}."
            )
        if "authorization failed" in reason:
            return f"Telegram listener {account} avtorizatsiyadan o‘ta olmadi. Hisobni tekshiring."
        if "connection failed" in reason:
            return (
                f"Telegram listener {account} Telegram bilan ulanishni yo‘qotdi "
                "va qayta ulanmoqda."
            )
        if "no recent successful events" in reason:
            return f"Telegram listener {account} dan yaqinda muvaffaqiyatli xabar kelmadi."
        if "health changed to" in reason:
            status = reason.rsplit("health changed to", 1)[-1].strip()
            return f"Telegram listener {account} holati o‘zgardi: {_readable_status(status)}."
        if "will be reassigned" in reason:
            return f"{account} listener ishlamayapti. Kanal boshqa listener'ga biriktiriladi."

    if reason.startswith("Telegram listener reload skipped"):
        return (
            "Telegram listenerlarni yangilashda ayrim manbalar o‘tkazib yuborildi. "
            "Admin panelni tekshiring."
        )

    # Keep unexpected alerts understandable without exposing raw secret values.
    readable = reason.replace("_", " ").replace("=", ": ")
    return f"Tizim xabari: {readable}"


class OpsNotificationService(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def notify(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> bool: ...


class DisabledOpsNotificationService:
    @property
    def enabled(self) -> bool:
        return False

    async def notify(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> bool:
        return False


@dataclass
class _AlertBucket:
    first_seen: float
    last_sent: float
    suppressed_count: int = 0


class TelegramOpsNotificationService:
    def __init__(
        self,
        *,
        bot_token: SecretStr,
        chat_id: str,
        rate_limit_window_seconds: int = 300,
        session: ClientSession | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._rate_limit_window_seconds = rate_limit_window_seconds
        self._buckets: dict[tuple[str, str], _AlertBucket] = {}
        self._session = session
        self._blocked_until = 0.0

    @property
    def enabled(self) -> bool:
        return True

    async def notify(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> bool:
        now = time.monotonic()
        if now < self._blocked_until:
            return False
        normalized_severity = severity.upper()
        sanitized_reason = redact_text(reason)
        bucket_key = (normalized_severity, sanitized_reason)
        bucket = self._buckets.get(bucket_key)
        if bucket and now - bucket.last_sent < self._rate_limit_window_seconds:
            bucket.suppressed_count += 1
            return False

        suppressed_count = bucket.suppressed_count if bucket else 0
        self._buckets[bucket_key] = _AlertBucket(first_seen=now, last_sent=now)
        message = self._format_message(
            severity=normalized_severity,
            reason=sanitized_reason,
            correlation_id=correlation_id or get_correlation_id(),
            suppressed_count=suppressed_count,
        )
        try:
            result = await self._send_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Ops notification failed without affecting the main workflow: %s",
                type(exc).__name__,
            )
            return False
        if result is not None:
            status, retry_after = result
            if status == 429:
                self._blocked_until = time.monotonic() + max(retry_after or 60, 1)
                logger.warning(
                    "Ops notification rate limited; entering cooldown for %s seconds",
                    retry_after or 60,
                )
                return False
            if status >= 400:
                logger.warning("Ops notification rejected with HTTP status %s", status)
                return False
        return True

    def _format_message(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str,
        suppressed_count: int,
    ) -> str:
        lines = [
            "EstateFlow OPS",
            f"Holat: {_severity_label(severity)}",
            f"Xabar: {humanize_ops_reason(reason)}",
            f"Vaqt: {datetime.now(UTC).isoformat()}",
            f"Tekshiruv ID: {correlation_id}",
        ]
        if suppressed_count:
            lines.append(f"Takroriy xabarlar soni: {suppressed_count}")
        return "\n".join(lines)

    async def _send_message(self, text: str) -> tuple[int, int | None] | None:
        token = self._bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text}
        if self._session is not None:
            async with self._session.post(url, json=payload) as response:
                return response.status, _retry_after_seconds(response.headers.get("Retry-After"))
            return
        async with ClientSession() as session:
            async with session.post(url, json=payload) as response:
                return response.status, _retry_after_seconds(response.headers.get("Retry-After"))


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(int(value), 1)
    except ValueError:
        return None


def _severity_label(severity: str) -> str:
    return {
        "INFO": "Ma’lumot",
        "WARNING": "Ogohlantirish",
        "CRITICAL": "Jiddiy xatolik",
    }.get(severity, severity.title())


def create_ops_notification_service(
    *, ops_bot_token: SecretStr | None, ops_chat_id: str | None
) -> OpsNotificationService:
    if ops_bot_token is None or not ops_chat_id:
        return DisabledOpsNotificationService()
    return TelegramOpsNotificationService(bot_token=ops_bot_token, chat_id=ops_chat_id)
