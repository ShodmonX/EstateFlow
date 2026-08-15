from __future__ import annotations

from decimal import Decimal

from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.search import SearchResult


def render_announcement_card(item: StructuredAnnouncement) -> str:
    canonical = item.canonical
    price = _price(
        canonical.price_normalized_monthly,
        canonical.currency,
        canonical.price_basis,
    )
    lines = [
        f"Narx: {price}",
        f"Davr/asos: {canonical.price_period}/{canonical.price_basis}",
        f"Tuman: {_unknown(canonical.district)}",
        f"Xona: {canonical.rooms if canonical.rooms is not None else _unknown(None)}",
        f"Remont: {_unknown(canonical.renovation_level)}",
        f"Manbalar: {item.source_count}",
        f"Rasm: {'bor' if item.media else 'yoq'}",
    ]
    if item.source_url:
        lines.append(f"Link: {item.source_url}")
    description = " ".join(canonical.description.split())
    if description:
        lines.append(description[:280])
    return "\n".join(lines)[:900]


def render_search_page(result: SearchResult) -> str:
    if not result.items:
        return "Mos e'lon topilmadi. Filtrlarni o'zgartiring yoki reset qiling."
    cards = [render_announcement_card(item) for item in result.items]
    return "\n\n---\n\n".join(cards)[:3900]


def _price(value: Decimal | None, currency: str | None, basis: str) -> str:
    if value is None:
        return "Noma'lum"
    suffix = "/kishiga" if basis == "per_person" else "/oy"
    return f"{value:g} {currency or ''}{suffix}".strip()


def _unknown(value: object | None) -> object:
    return "Noma'lum" if value is None or value == "" else value
