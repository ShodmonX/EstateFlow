from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from redis.asyncio import Redis
from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.errors import (  # type: ignore[import-untyped]
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    RPCError,
    SessionPasswordNeededError,
    UnauthorizedError,
)

from estateflow.application.core.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_AUTH_KEY_PREFIX = "estateflow:telegram-auth"
TELEGRAM_AUTH_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class TelegramAuthSessionState:
    session_name: str
    phone_number: str
    phone_code_hash: str | None
    created_at: datetime
    updated_at: datetime
    requires_password: bool = False


@dataclass(frozen=True)
class TelegramAuthStartResult:
    session_name: str
    phone_number: str
    code_sent: bool
    already_authorized: bool


@dataclass(frozen=True)
class TelegramAuthCodeResult:
    session_name: str
    authorized: bool
    requires_password: bool


@dataclass(frozen=True)
class TelegramAuthPasswordResult:
    session_name: str
    authorized: bool


@dataclass(frozen=True)
class TelegramSessionStatus:
    session_name: str
    session_path: str
    session_file_path: str
    file_exists: bool
    file_size_bytes: int | None
    authorized: bool | None
    auth_state_present: bool
    requires_password: bool
    updated_at: datetime | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class TelegramSourceAccessCheck:
    session_name: str
    source_identifier: str
    accessible: bool
    status: str
    error: str | None = None
    resolved_channel_id: str | None = None


class TelegramAuthStore(Protocol):
    async def get(self, session_name: str) -> TelegramAuthSessionState | None: ...

    async def put(self, state: TelegramAuthSessionState, *, ttl_seconds: int) -> None: ...

    async def delete(self, session_name: str) -> None: ...


class RedisTelegramAuthStore:
    def __init__(self, redis: Redis, *, prefix: str = TELEGRAM_AUTH_KEY_PREFIX) -> None:
        self._redis = redis
        self._prefix = prefix

    async def get(self, session_name: str) -> TelegramAuthSessionState | None:
        payload = await self._redis.get(self._key(session_name))
        if payload is None:
            return None
        data = json.loads(payload)
        return TelegramAuthSessionState(
            session_name=str(data["session_name"]),
            phone_number=str(data["phone_number"]),
            phone_code_hash=str(data["phone_code_hash"]) if data.get("phone_code_hash") else None,
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            requires_password=bool(data.get("requires_password", False)),
        )

    async def put(self, state: TelegramAuthSessionState, *, ttl_seconds: int) -> None:
        payload = json.dumps(
            {
                "session_name": state.session_name,
                "phone_number": state.phone_number,
                "phone_code_hash": state.phone_code_hash,
                "created_at": state.created_at.isoformat(),
                "updated_at": state.updated_at.isoformat(),
                "requires_password": state.requires_password,
            }
        )
        await self._redis.set(self._key(state.session_name), payload, ex=ttl_seconds)

    async def delete(self, session_name: str) -> None:
        await self._redis.delete(self._key(session_name))

    def _key(self, session_name: str) -> str:
        return f"{self._prefix}:{session_name}"


class TelegramAuthService:
    def __init__(
        self,
        settings: Settings,
        *,
        store: TelegramAuthStore,
        session_dir: Path | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._session_dir = _resolve_session_dir(session_dir or settings.telegram_session_dir)
        self._client_factory = client_factory or TelegramClient
        self._session_dir.mkdir(parents=True, exist_ok=True)

    async def start_login(
        self,
        *,
        session_name: str,
        phone_number: str,
        force_sms: bool = False,
    ) -> TelegramAuthStartResult:
        client = self._new_client(session_name)
        try:
            await client.connect()
        except (sqlite3.OperationalError, OSError) as exc:
            if "locked" in str(exc).lower() or "lock" in str(exc).lower():
                raise RuntimeError(
                    f"Session '{session_name}' file is locked by an active listener or process."
                ) from exc
            raise

        try:
            if await client.is_user_authorized():
                return TelegramAuthStartResult(
                    session_name=session_name,
                    phone_number=phone_number,
                    code_sent=False,
                    already_authorized=True,
                )
            sent = await client.send_code_request(phone_number, force_sms=force_sms)
            now = datetime.now(UTC)
            await self._store.put(
                TelegramAuthSessionState(
                    session_name=session_name,
                    phone_number=phone_number,
                    phone_code_hash=getattr(sent, "phone_code_hash", None),
                    created_at=now,
                    updated_at=now,
                    requires_password=False,
                ),
                ttl_seconds=TELEGRAM_AUTH_TTL_SECONDS,
            )
            return TelegramAuthStartResult(
                session_name=session_name,
                phone_number=phone_number,
                code_sent=True,
                already_authorized=False,
            )
        except FloodWaitError as exc:
            raise RuntimeError(f"Telegram rate limited login for {exc.seconds} seconds.") from exc
        except PhoneNumberInvalidError as exc:
            raise ValueError("The phone number provided is invalid.") from exc
        except ApiIdInvalidError as exc:
            raise RuntimeError("Telegram API credentials (api_id / api_hash) are invalid.") from exc
        except (RPCError, UnauthorizedError) as exc:
            raise RuntimeError(f"Telegram start login failed: {exc}") from exc
        finally:
            await client.disconnect()

    async def confirm_code(
        self,
        *,
        session_name: str,
        code: str,
    ) -> TelegramAuthCodeResult:
        state = await self._require_state(session_name)
        client = self._new_client(session_name)
        try:
            await client.connect()
        except (sqlite3.OperationalError, OSError) as exc:
            if "locked" in str(exc).lower() or "lock" in str(exc).lower():
                raise RuntimeError(
                    f"Session '{session_name}' file is locked by an active listener or process."
                ) from exc
            raise

        try:
            try:
                await client.sign_in(
                    state.phone_number,
                    code,
                    phone_code_hash=state.phone_code_hash,
                )
                await self._store.delete(session_name)
                await _send_saved_messages_log(client, session_name)
                return TelegramAuthCodeResult(
                    session_name=session_name,
                    authorized=True,
                    requires_password=False,
                )
            except SessionPasswordNeededError:
                await self._store.put(
                    TelegramAuthSessionState(
                        session_name=state.session_name,
                        phone_number=state.phone_number,
                        phone_code_hash=state.phone_code_hash,
                        created_at=state.created_at,
                        updated_at=datetime.now(UTC),
                        requires_password=True,
                    ),
                    ttl_seconds=TELEGRAM_AUTH_TTL_SECONDS,
                )
                return TelegramAuthCodeResult(
                    session_name=session_name,
                    authorized=False,
                    requires_password=True,
                )
            except (PhoneCodeInvalidError, PhoneCodeEmptyError) as exc:
                raise ValueError("The phone code entered was invalid.") from exc
            except PhoneCodeExpiredError as exc:
                raise ValueError("The phone code has expired. Please request a new code.") from exc
            except FloodWaitError as exc:
                raise RuntimeError(
                    f"Telegram rate limited login for {exc.seconds} seconds."
                ) from exc
            except UnauthorizedError as exc:
                raise RuntimeError("Telegram authentication rejected the login code.") from exc
            except RPCError as exc:
                raise RuntimeError(f"Telegram login code error: {exc}") from exc
        finally:
            await client.disconnect()

    async def confirm_password(
        self,
        *,
        session_name: str,
        password: str,
    ) -> TelegramAuthPasswordResult:
        state = await self._require_state(session_name)
        if not state.requires_password:
            raise ValueError("2FA password is not required for this session.")
        client = self._new_client(session_name)
        try:
            await client.connect()
        except (sqlite3.OperationalError, OSError) as exc:
            if "locked" in str(exc).lower() or "lock" in str(exc).lower():
                raise RuntimeError(
                    f"Session '{session_name}' file is locked by an active listener or process."
                ) from exc
            raise

        try:
            try:
                await client.sign_in(password=password)
                await self._store.delete(session_name)
                await _send_saved_messages_log(client, session_name)
                return TelegramAuthPasswordResult(
                    session_name=session_name,
                    authorized=True,
                )
            except PasswordHashInvalidError as exc:
                raise ValueError("The 2FA password entered was invalid.") from exc
            except FloodWaitError as exc:
                raise RuntimeError(
                    f"Telegram rate limited login for {exc.seconds} seconds."
                ) from exc
            except (UnauthorizedError, RPCError) as exc:
                raise RuntimeError(f"Telegram 2FA password login failed: {exc}") from exc
        finally:
            await client.disconnect()

    async def get_state(self, session_name: str) -> TelegramAuthSessionState | None:
        return await self._store.get(session_name)

    async def delete_state(self, session_name: str) -> None:
        await self._store.delete(session_name)

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def session_file_path(self, session_name: str) -> Path:
        return self._session_path(session_name).with_suffix(".session")

    async def list_session_names(self) -> list[str]:
        names: set[str] = set()
        if self._session_dir.exists():
            for path in self._session_dir.glob("*.session"):
                names.add(path.stem)
        return sorted(names)

    async def inspect_session(self, session_name: str) -> TelegramSessionStatus:
        session_file_path = self.session_file_path(session_name)
        file_exists = session_file_path.exists()
        file_size_bytes = session_file_path.stat().st_size if file_exists else None
        state = await self._store.get(session_name)
        authorized: bool | None = None
        status = "missing"
        error: str | None = None
        if not file_exists:
            if state is not None and state.requires_password:
                status = "needs_password"
            elif state is not None:
                status = "pending_auth"
            return TelegramSessionStatus(
                session_name=session_name,
                session_path=str(self._session_path(session_name)),
                session_file_path=str(session_file_path),
                file_exists=False,
                file_size_bytes=None,
                authorized=None,
                auth_state_present=state is not None,
                requires_password=bool(state.requires_password) if state else False,
                updated_at=state.updated_at if state else None,
                status=status,
                error=None,
            )

        client = self._new_client(session_name)
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
            if state is not None and state.requires_password:
                status = "needs_password"
            else:
                status = "ready" if authorized else "unauthorized"
        except (sqlite3.OperationalError, OSError) as exc:
            status = "locked"
            error = str(exc)
        except (RPCError, UnauthorizedError) as exc:
            status = "error"
            error = str(exc)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        return TelegramSessionStatus(
            session_name=session_name,
            session_path=str(self._session_path(session_name)),
            session_file_path=str(session_file_path),
            file_exists=True,
            file_size_bytes=file_size_bytes,
            authorized=authorized,
            auth_state_present=state is not None,
            requires_password=bool(state.requires_password) if state else False,
            updated_at=state.updated_at if state else None,
            status=status,
            error=error,
        )

    async def verify_source_access(
        self,
        *,
        session_name: str,
        source_identifier: str,
    ) -> TelegramSourceAccessCheck:
        status = await self.inspect_session(session_name)
        if status.status != "ready":
            raise PermissionError(
                f"Telegram session '{session_name}' is not ready for source access: "
                f"{status.status}."
            )
        client = self._new_client(session_name)
        try:
            await client.connect()
            entity = await client.get_entity(source_identifier)
            resolved_channel_id: str | None = None
            entity_id = getattr(entity, "id", None)
            if entity_id is not None:
                raw_id = str(entity_id)
                if not raw_id.startswith("-"):
                    resolved_channel_id = f"-100{raw_id}"
                else:
                    resolved_channel_id = raw_id
            return TelegramSourceAccessCheck(
                session_name=session_name,
                source_identifier=source_identifier,
                accessible=True,
                status="accessible",
                resolved_channel_id=resolved_channel_id,
            )
        except (
            ValueError,
            PhoneNumberInvalidError,
            FloodWaitError,
            RPCError,
            UnauthorizedError,
        ) as exc:
            raise PermissionError(
                f"Telegram session '{session_name}' cannot access {source_identifier}: {exc}"
            ) from exc
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    def session_path(self, session_name: str) -> Path:
        return self._session_path(session_name)

    def _new_client(self, session_name: str) -> TelegramClient:
        api_id = self._settings.telegram_api_id
        api_hash = self._settings.telegram_api_hash
        if api_id is None or api_hash is None:
            raise RuntimeError("Telegram API credentials are not configured.")
        return self._client_factory(
            self._session_path(session_name),
            api_id,
            api_hash.get_secret_value(),
        )

    def _session_path(self, session_name: str) -> Path:
        safe_name = _sanitize_session_name(session_name)
        return self._session_dir / safe_name

    async def _require_state(self, session_name: str) -> TelegramAuthSessionState:
        state = await self._store.get(session_name)
        if state is None:
            raise KeyError(session_name)
        return state


async def _send_saved_messages_log(client: Any, session_name: str) -> None:
    try:
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        msg = (
            f"EstateFlow: Account session '{session_name}' successfully logged in "
            f"and authorized at {now_str}."
        )
        if hasattr(client, "send_message"):
            await client.send_message("me", msg)
    except Exception as exc:
        logger.warning("Failed to send login notification to Saved Messages: %s", exc)


def _sanitize_session_name(session_name: str) -> str:
    normalized = session_name.strip()
    if not normalized:
        raise ValueError("session_name is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", normalized):
        raise ValueError("session_name must use only letters, digits, dot, dash and underscore")
    return normalized


def _resolve_session_dir(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path
