from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

CALLBACK_VERSION = "v1"
DEFAULT_CALLBACK_TTL_SECONDS = 24 * 60 * 60


MENU_ACTIONS = frozenset({"search", "filters", "mini_app", "soon", "help", "back", "cancel"})
NLP_ACTIONS = frozenset({"start", "confirm", "edit", "cancel"})
WIZARD_ACTIONS = frozenset(
    {
        "start",
        "skip",
        "back",
        "cancel",
        "basis_total",
        "basis_any",
        "renovation",
        "audience",
        "page",
        "reset",
    }
)
FILTER_ACTIONS = frozenset(
    {
        "list",
        "view",
        "save",
        "run",
        "edit",
        "update",
        "enable",
        "disable",
        "delete_confirm",
        "delete",
        "cancel",
    }
)
SOURCE_ACTIONS = frozenset({"start", "cancel"})
REFERRAL_ACTIONS = frozenset({"link"})


@dataclass(frozen=True)
class CallbackPayload:
    namespace: str
    action: str
    owner_id: int
    value: str = ""
    issued_at: int = 0

    def pack(self) -> str:
        issued_at = self.issued_at or _now_ts()
        owner = _base36_encode(self.owner_id)
        issued = _base36_encode(issued_at)
        return f"{CALLBACK_VERSION}:{self.namespace}:{self.action}:{owner}:{issued}:{self.value}"

    @classmethod
    def unpack(
        cls,
        value: str,
        *,
        expected_owner_id: int | None = None,
        now: datetime | None = None,
        max_age_seconds: int = DEFAULT_CALLBACK_TTL_SECONDS,
    ) -> CallbackPayload:
        parts = value.split(":", 5)
        if len(parts) != 6 or parts[0] != CALLBACK_VERSION:
            raise ValueError("Unsupported callback payload.")
        try:
            owner_id = _base36_decode(parts[3])
            issued_at = _base36_decode(parts[4])
        except ValueError as exc:
            raise ValueError("Invalid callback payload.") from exc
        if expected_owner_id is not None and owner_id != expected_owner_id:
            raise PermissionError("Callback owner mismatch.")
        _validate_allowed(parts[1], parts[2])
        current_ts = int((now or datetime.now(UTC)).timestamp())
        if issued_at > current_ts + 60 or current_ts - issued_at > max_age_seconds:
            raise TimeoutError("Callback payload expired.")
        return cls(
            namespace=parts[1],
            action=parts[2],
            owner_id=owner_id,
            issued_at=issued_at,
            value=parts[5],
        )


def callback(namespace: str, action: str, *, owner_id: int, value: str = "") -> str:
    return CallbackPayload(
        namespace=namespace,
        action=action,
        owner_id=owner_id,
        value=value,
    ).pack()


def _validate_allowed(namespace: str, action: str) -> None:
    if namespace == "menu" and action in MENU_ACTIONS:
        return
    if namespace == "nlp" and action in NLP_ACTIONS:
        return
    if namespace == "wizard" and action in WIZARD_ACTIONS:
        return
    if namespace == "filter" and action in FILTER_ACTIONS:
        return
    if namespace == "source" and action in SOURCE_ACTIONS:
        return
    if namespace == "referral" and action in REFERRAL_ACTIONS:
        return
    raise ValueError("Unsupported callback action.")


def _now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


def _base36_encode(value: int) -> str:
    if value < 0:
        raise ValueError("Cannot encode negative values.")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    digits: list[str] = []
    current = value
    while current:
        current, remainder = divmod(current, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _base36_decode(value: str) -> int:
    return int(value, 36)
