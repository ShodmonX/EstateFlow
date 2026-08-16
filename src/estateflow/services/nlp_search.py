from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from estateflow.services.ai_client import LLMError, LLMMessage, LLMRequest, LLMResponse
from estateflow.services.extraction import AUDIENCE_TAGS, RenovationLevel
from estateflow.services.search import SearchCriteria

NLP_SEARCH_SCHEMA_VERSION = "estateflow.search.nlp.v1"
MAX_NLP_QUERY_LENGTH = 140

PriceBasisPreference = Literal["total", "include_per_person"]

_DISTRICT_ALIASES: dict[str, str] = {
    "yunusobod": "Yunusobod",
    "yunusabad": "Yunusobod",
    "ю нусабад": "Yunusobod",
    "юнусабад": "Yunusobod",
    "юнисабад": "Yunusobod",
    "чиланзар": "Chilonzor",
    "чилонзор": "Chilonzor",
    "chilonzor": "Chilonzor",
    "chilanazar": "Chilonzor",
    "sergeli": "Sergeli",
    "сергели": "Sergeli",
    "mirzo ulugbek": "Mirzo Ulugbek",
    "mirzo ulug'bek": "Mirzo Ulugbek",
    "мирзо улугбек": "Mirzo Ulugbek",
    "yakkasaroy": "Yakkasaroy",
    "яккасарай": "Yakkasaroy",
    "shayxontohur": "Shayxontohur",
    "шайхантахур": "Shayxontohur",
    "olmazor": "Olmazor",
    "алмазар": "Olmazor",
    "uchtepa": "Uchtepa",
    "учтепа": "Uchtepa",
    "mirobod": "Mirobod",
    "мирабоад": "Mirobod",
    "миробод": "Mirobod",
    "yashnobod": "Yashnobod",
    "яшнабад": "Yashnobod",
    "bektemir": "Bektemir",
    "бектемир": "Bektemir",
}

_AUDIENCE_ALIASES: dict[str, str] = {
    "young_family": "family",
    "young family": "family",
    "yosh oila": "family",
    "ёш оила": "family",
    "молодая семья": "family",
    "семья": "family",
    "oila": "family",
    "family": "family",
    "students": "students",
    "student": "students",
    "talaba": "students",
    "talabalar": "students",
    "студенты": "students",
    "studenty": "students",
    "foreigners": "foreigners",
    "chet elliklar": "foreigners",
    "иностранцы": "foreigners",
}


class NlpSearchExtractionRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["estateflow.search.nlp.raw.v1"] = "estateflow.search.nlp.raw.v1"
    monthly_budget: Decimal | None = Field(default=None, ge=0)
    price_basis_preference: PriceBasisPreference = "total"
    districts: list[str] = Field(default_factory=list, max_length=5)
    rooms: int | None = Field(default=None, ge=0, le=20)
    renovation_level: RenovationLevel | None = None
    audience_tag: str | None = None
    unapplied_conditions: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.9, ge=0, le=1)

    @field_validator("districts", "unapplied_conditions")
    @classmethod
    def normalize_text_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            clean = " ".join(value.strip().split())
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized

    @field_validator("audience_tag")
    @classmethod
    def normalize_audience(cls, value: str | None) -> str | None:
        if value is None:
            return None
        tag = normalize_audience_tag(value)
        if tag is None:
            raise ValueError("Unknown audience tag.")
        return tag


@dataclass(frozen=True)
class NlpSearchExtraction:
    criteria: SearchCriteria
    applied_fields: tuple[str, ...]
    unapplied_conditions: tuple[str, ...]
    confidence: float


class NlpSearchExtractionFailedError(RuntimeError):
    pass


class NlpSearchLlmClient(Protocol):
    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]: ...


class NlpSearchExtractor:
    def __init__(self, *, llm_client: NlpSearchLlmClient, min_confidence: float = 0.25) -> None:
        self._llm_client = llm_client
        self._min_confidence = min_confidence

    async def extract(self, text: str) -> NlpSearchExtraction:
        normalized_text = validate_nlp_query_text(text)
        request = LLMRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "You extract EstateFlow rental search filters only. "
                        "Return strict JSON matching the schema. "
                        "Never create SQL, ORM clauses, callback data, or authorization decisions. "
                        "Supported fields: monthly budget, price basis preference, "
                        "districts, rooms, renovation level, audience tag, "
                        "and unapplied conditions. "
                        "Map young family/yosh oila/mолодая семья to family. "
                        "Put unsupported requirements such as metro proximity, amenities, pets, "
                        "or exact address into unapplied_conditions."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        "<untrusted_user_search_text>\n"
                        f"{normalized_text}\n"
                        "</untrusted_user_search_text>"
                    ),
                ),
            ],
            json_schema=nlp_search_json_schema(),
            correlation_id=uuid4().hex,
            min_confidence=self._min_confidence,
            temperature=0.0,
        )
        try:
            response, _attempts = await self._llm_client.complete_json(request)
            raw = NlpSearchExtractionRaw.model_validate(response.content)
            return normalize_nlp_search_extraction(raw)
        except (LLMError, ValidationError, ValueError) as exc:
            raise NlpSearchExtractionFailedError("NLP search extraction failed.") from exc


def validate_nlp_query_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("Qidiruv matnini yozing.")
    if len(normalized) > MAX_NLP_QUERY_LENGTH:
        raise ValueError("Erkin qidiruv matni 140 belgidan oshmasin.")
    return normalized


def nlp_search_json_schema() -> dict[str, Any]:
    return NlpSearchExtractionRaw.model_json_schema()


def normalize_nlp_search_extraction(raw: NlpSearchExtractionRaw) -> NlpSearchExtraction:
    applied: list[str] = []
    unapplied = list(raw.unapplied_conditions)
    districts = [district for value in raw.districts if (district := normalize_district(value))]
    district = districts[0] if districts else None
    if district is not None:
        applied.append("districts")

    if raw.rooms is not None:
        applied.append("rooms")
    if raw.monthly_budget is not None:
        applied.append("max_price")
    if raw.price_basis_preference == "include_per_person":
        applied.append("include_per_person")
    if raw.renovation_level is not None:
        applied.append("renovation_level")
    if raw.audience_tag is not None:
        applied.append("audience_tag")

    criteria = SearchCriteria(
        max_price=raw.monthly_budget,
        include_per_person=raw.price_basis_preference == "include_per_person",
        district=district,
        districts=districts,
        rooms=raw.rooms,
        renovation_level=raw.renovation_level,
        audience_tag=raw.audience_tag,
    )
    return NlpSearchExtraction(
        criteria=criteria,
        applied_fields=tuple(applied),
        unapplied_conditions=tuple(_dedupe(unapplied)),
        confidence=raw.confidence,
    )


def normalize_district(value: str) -> str | None:
    key = " ".join(value.strip().casefold().split())
    return _DISTRICT_ALIASES.get(key)


def normalize_audience_tag(value: str) -> str | None:
    key = " ".join(value.strip().casefold().split())
    tag = _AUDIENCE_ALIASES.get(key, key)
    return tag if tag in AUDIENCE_TAGS else None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        clean = " ".join(value.strip().split())
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped
