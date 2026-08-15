from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Literal, Protocol, TypedDict

AudienceTagStatus = Literal["approved", "pending", "rejected"]
AudienceTagAction = Literal["approve", "merge", "reject"]

SEED_AUDIENCE_TAGS: dict[str, str] = {
    "family": "Oila",
    "family_with_children": "Bolali oila",
    "students": "Talabalar",
    "single_male": "Yolg'iz erkak",
    "single_female": "Yolg'iz ayol",
    "group_of_girls": "Qizlar guruhi",
    "group_of_boys": "Yigitlar guruhi",
    "foreigners": "Chet elliklar",
}

TAG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
TAG_ALIAS_MAP = {
    "young_family": "family",
    "yosh_oila": "family",
}


@dataclass(frozen=True)
class AudienceTagType:
    tag_key: str
    display_name_uz: str
    status: AudienceTagStatus = "pending"
    usage_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class AudienceTagAuditEvent:
    admin_user_id: int
    action: AudienceTagAction
    idempotency_key: str
    tag_key: str
    target_tag_key: str | None = None
    note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class AudienceTagResolveResult:
    input_tag: str
    tag_key: str
    status: AudienceTagStatus
    created: bool = False
    matched_existing: bool = False


@dataclass(frozen=True)
class AudienceTagReferenceRemapResult:
    announcements_updated: int = 0
    filters_updated: int = 0


class ResolvedAudienceTags(TypedDict):
    approved_tags: list[str]
    audit_assumptions: list[str]


class AudienceTagRepository(Protocol):
    async def list_approved(self, *, limit: int | None = None) -> tuple[AudienceTagType, ...]: ...

    async def list_pending(self) -> tuple[AudienceTagType, ...]: ...

    async def get(self, *, tag_key: str) -> AudienceTagType | None: ...

    async def upsert_pending(
        self,
        *,
        tag_key: str,
        display_name_uz: str,
    ) -> tuple[AudienceTagType, bool]: ...

    async def increment_usage(self, *, tag_key: str) -> AudienceTagType: ...

    async def set_status(
        self,
        *,
        tag_key: str,
        status: AudienceTagStatus,
        display_name_uz: str | None = None,
    ) -> AudienceTagType: ...

    async def audit_once(
        self,
        event: AudienceTagAuditEvent,
    ) -> tuple[AudienceTagAuditEvent, bool]: ...

    async def remap_references(
        self,
        *,
        source_tag_key: str,
        target_tag_key: str,
    ) -> AudienceTagReferenceRemapResult: ...


class InMemoryAudienceTagRepository:
    def __init__(self, tags: dict[str, str] | None = None) -> None:
        self.tags: dict[str, AudienceTagType] = {
            key: AudienceTagType(tag_key=key, display_name_uz=name, status="approved")
            for key, name in (tags or SEED_AUDIENCE_TAGS).items()
        }
        self.audit_events: dict[str, AudienceTagAuditEvent] = {}
        self.announcement_refs: dict[str, set[str]] = {}
        self.announcement_excluded_refs: dict[str, set[str]] = {}
        self.filter_refs: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def list_approved(self, *, limit: int | None = None) -> tuple[AudienceTagType, ...]:
        approved = [tag for tag in self.tags.values() if tag.status == "approved"]
        approved.sort(key=lambda tag: (-tag.usage_count, tag.tag_key))
        return tuple(approved[:limit] if limit is not None else approved)

    async def list_pending(self) -> tuple[AudienceTagType, ...]:
        pending = [tag for tag in self.tags.values() if tag.status == "pending"]
        pending.sort(key=lambda tag: (tag.created_at, tag.tag_key))
        return tuple(pending)

    async def get(self, *, tag_key: str) -> AudienceTagType | None:
        return self.tags.get(tag_key)

    async def upsert_pending(
        self,
        *,
        tag_key: str,
        display_name_uz: str,
    ) -> tuple[AudienceTagType, bool]:
        async with self._lock:
            existing = self.tags.get(tag_key)
            if existing is not None:
                return existing, False
            tag = AudienceTagType(tag_key=tag_key, display_name_uz=display_name_uz)
            self.tags[tag_key] = tag
            return tag, True

    async def increment_usage(self, *, tag_key: str) -> AudienceTagType:
        async with self._lock:
            tag = self.tags[tag_key]
            updated = replace(
                tag,
                usage_count=tag.usage_count + 1,
                updated_at=datetime.now(UTC),
            )
            self.tags[tag_key] = updated
            return updated

    async def set_status(
        self,
        *,
        tag_key: str,
        status: AudienceTagStatus,
        display_name_uz: str | None = None,
    ) -> AudienceTagType:
        async with self._lock:
            tag = self.tags[tag_key]
            updated = replace(
                tag,
                status=status,
                display_name_uz=display_name_uz or tag.display_name_uz,
                updated_at=datetime.now(UTC),
            )
            self.tags[tag_key] = updated
            return updated

    async def audit_once(
        self,
        event: AudienceTagAuditEvent,
    ) -> tuple[AudienceTagAuditEvent, bool]:
        async with self._lock:
            existing = self.audit_events.get(event.idempotency_key)
            if existing is not None:
                return existing, False
            self.audit_events[event.idempotency_key] = event
            return event, True

    async def remap_references(
        self,
        *,
        source_tag_key: str,
        target_tag_key: str,
    ) -> AudienceTagReferenceRemapResult:
        async with self._lock:
            announcements_updated = 0
            for refs in (self.announcement_refs, self.announcement_excluded_refs):
                for key, tags in refs.items():
                    if source_tag_key in tags:
                        tags.discard(source_tag_key)
                        tags.add(target_tag_key)
                        refs[key] = tags
                        announcements_updated += 1
            filters_updated = 0
            for filter_id, tag_key in list(self.filter_refs.items()):
                if tag_key == source_tag_key:
                    self.filter_refs[filter_id] = target_tag_key
                    filters_updated += 1
            return AudienceTagReferenceRemapResult(
                announcements_updated=announcements_updated,
                filters_updated=filters_updated,
            )


class AudienceTagService:
    def __init__(
        self,
        repository: AudienceTagRepository,
        *,
        fuzzy_threshold: float = 0.82,
    ) -> None:
        self._repository = repository
        self._fuzzy_threshold = fuzzy_threshold

    async def prompt_tags(self, *, limit: int = 15) -> tuple[str, ...]:
        return tuple(tag.tag_key for tag in await self._repository.list_approved(limit=limit))

    async def list_pending(self) -> tuple[AudienceTagType, ...]:
        return await self._repository.list_pending()

    async def get(self, *, tag_key: str) -> AudienceTagType | None:
        return await self._repository.get(tag_key=normalize_tag_key(tag_key))

    async def resolve_tags(self, values: list[str]) -> tuple[AudienceTagResolveResult, ...]:
        results: list[AudienceTagResolveResult] = []
        seen: set[str] = set()
        for value in values:
            key = normalize_tag_key(value)
            if key in seen:
                continue
            seen.add(key)
            results.append(await self.resolve_tag(key))
        return tuple(results)

    async def resolve_for_canonical(self, values: list[str]) -> ResolvedAudienceTags:
        approved_tags: list[str] = []
        audit_assumptions: list[str] = []
        for result in await self.resolve_tags(values):
            if result.status == "approved":
                if result.tag_key not in approved_tags:
                    approved_tags.append(result.tag_key)
                if result.input_tag != result.tag_key:
                    audit_assumptions.append(
                        f"audience_tag_mapped:{result.input_tag}->{result.tag_key}"
                    )
            elif result.status == "pending":
                audit_assumptions.append(f"pending_audience_tag:{result.tag_key}")
            else:
                audit_assumptions.append(f"rejected_audience_tag:{result.tag_key}")
        return {"approved_tags": approved_tags, "audit_assumptions": audit_assumptions}

    async def resolve_tag(self, value: str) -> AudienceTagResolveResult:
        key = normalize_tag_key(value)
        existing = await self._repository.get(tag_key=key)
        if existing is not None and existing.status != "rejected":
            await self._repository.increment_usage(tag_key=existing.tag_key)
            return AudienceTagResolveResult(
                input_tag=value,
                tag_key=existing.tag_key,
                status=existing.status,
                matched_existing=True,
            )

        fuzzy = await self._fuzzy_match(key)
        if fuzzy is not None:
            await self._repository.increment_usage(tag_key=fuzzy.tag_key)
            return AudienceTagResolveResult(
                input_tag=value,
                tag_key=fuzzy.tag_key,
                status=fuzzy.status,
                matched_existing=True,
            )

        tag, created = await self._repository.upsert_pending(
            tag_key=key,
            display_name_uz=key.replace("_", " ").title(),
        )
        return AudienceTagResolveResult(
            input_tag=value,
            tag_key=tag.tag_key,
            status=tag.status,
            created=created,
        )

    async def approve(
        self,
        *,
        tag_key: str,
        admin_user_id: int,
        idempotency_key: str,
        display_name_uz: str | None = None,
        expected_status: AudienceTagStatus | None = "pending",
    ) -> AudienceTagType:
        event, created = await self._repository.audit_once(
            AudienceTagAuditEvent(
                admin_user_id=admin_user_id,
                action="approve",
                idempotency_key=idempotency_key,
                tag_key=normalize_tag_key(tag_key),
            )
        )
        tag = await self._repository.get(tag_key=event.tag_key)
        if tag is None:
            raise KeyError(event.tag_key)
        if not created:
            return tag
        if expected_status is not None and tag.status != expected_status:
            raise ValueError("audience_tag_state_conflict")
        return await self._repository.set_status(
            tag_key=event.tag_key,
            status="approved",
            display_name_uz=display_name_uz,
        )

    async def reject(
        self,
        *,
        tag_key: str,
        admin_user_id: int,
        idempotency_key: str,
        note: str | None = None,
        expected_status: AudienceTagStatus | None = "pending",
    ) -> AudienceTagType:
        event, created = await self._repository.audit_once(
            AudienceTagAuditEvent(
                admin_user_id=admin_user_id,
                action="reject",
                idempotency_key=idempotency_key,
                tag_key=normalize_tag_key(tag_key),
                note=note,
            )
        )
        tag = await self._repository.get(tag_key=event.tag_key)
        if tag is None:
            raise KeyError(event.tag_key)
        if not created:
            return tag
        if expected_status is not None and tag.status != expected_status:
            raise ValueError("audience_tag_state_conflict")
        return await self._repository.set_status(tag_key=event.tag_key, status="rejected")

    async def merge(
        self,
        *,
        tag_key: str,
        target_tag_key: str,
        admin_user_id: int,
        idempotency_key: str,
        note: str | None = None,
        expected_status: AudienceTagStatus | None = "pending",
    ) -> AudienceTagType:
        source_key = normalize_tag_key(tag_key)
        target_key = normalize_tag_key(target_tag_key)
        if source_key == target_key:
            raise ValueError("Cannot merge a tag into itself.")
        event, created = await self._repository.audit_once(
            AudienceTagAuditEvent(
                admin_user_id=admin_user_id,
                action="merge",
                idempotency_key=idempotency_key,
                tag_key=source_key,
                target_tag_key=target_key,
                note=note,
            )
        )
        source = await self._repository.get(tag_key=event.tag_key)
        target = await self._repository.get(tag_key=target_key)
        if source is None or target is None or target.status != "approved":
            raise KeyError("source_or_target_tag")
        if not created:
            return source
        if expected_status is not None and source.status != expected_status:
            raise ValueError("audience_tag_state_conflict")
        await self._repository.remap_references(
            source_tag_key=event.tag_key,
            target_tag_key=target_key,
        )
        await self._repository.increment_usage(tag_key=target_key)
        return await self._repository.set_status(tag_key=event.tag_key, status="rejected")

    async def _fuzzy_match(self, key: str) -> AudienceTagType | None:
        approved = await self._repository.list_approved(limit=None)
        best: tuple[float, AudienceTagType] | None = None
        for tag in approved:
            score = SequenceMatcher(a=key, b=tag.tag_key).ratio()
            if best is None or score > best[0]:
                best = (score, tag)
        if best is not None and best[0] >= self._fuzzy_threshold:
            return best[1]
        return None


def normalize_tag_key(value: str) -> str:
    key = value.strip().casefold().replace("-", "_").replace(" ", "_")
    key = re.sub(r"[^a-z0-9_]", "", key)
    key = re.sub(r"_+", "_", key).strip("_")
    key = TAG_ALIAS_MAP.get(key, key)
    if not TAG_KEY_RE.fullmatch(key):
        raise ValueError(f"Invalid audience tag key: {value}")
    return key
