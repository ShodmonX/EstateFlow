"""Offline Task001 JSON contracts; no domain implementation or remote registry."""

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
from functools import lru_cache
from jsonschema import Draft202012Validator, FormatChecker, RefResolver

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts/v2/schemas"


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, "efv2:" + ":".join(map(str, parts))))


def now():
    return datetime.now(timezone.utc).isoformat()


def new_event(
    event_type,
    producer,
    aggregate_kind,
    aggregate_id,
    version,
    payload,
    causation_id=None,
    correlation_id=None,
):
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "event_version": 1,
        "producer": producer,
        "occurred_at": now(),
        "recorded_at": now(),
        "correlation_id": correlation_id or str(uuid4()),
        "causation_id": causation_id,
        "aggregate_kind": aggregate_kind,
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
        "payload": payload,
    }


def offline(uri):
    raise ValueError("remote_schema_forbidden")


@lru_cache(maxsize=128)
def validator(filename):
    store = {x["$id"]: x for p in SCHEMAS.glob("*.json") if (x := json.loads(p.read_text()))}
    schema = json.loads((SCHEMAS / filename).read_text())
    return Draft202012Validator(
        schema,
        resolver=RefResolver.from_schema(
            schema, store=store, handlers={"http": offline, "https": offline}
        ),
        format_checker=FormatChecker(),
    )


def validate(event, semantic=True):
    try:
        name = event["event_type"]
        version = event["event_version"]
        if not isinstance(name, str) or any(c not in "abcdefghijklmnopqrstuvwxyz._" for c in name):
            raise ValueError("event_type")
        v = validator(f"{name}.v{version}.schema.json")
        errors = list(v.iter_errors(event))
        if errors:
            raise ValueError("schema_invalid")
        if not semantic:
            return
        p = event["payload"]
        kind = event["aggregate_kind"]
        ids = {
            "source_message": (
                p.get("source", {}).get("message_uid"),
                p.get("source_revision"),
            ),
            "album": (p.get("album_id"), p.get("album_revision")),
            "candidate": (
                p.get("lineage", {}).get("candidate_id"),
                p.get("lineage", {}).get("candidate_version"),
            ),
            "parser_batch": (
                p.get("batch_id"),
                p.get("generation", event["aggregate_version"]),
            ),
            "media_job": (p.get("media_job_id"), p.get("media_revision")),
            "canonical_listing": (p.get("canonical_id"), p.get("canonical_revision")),
            "command": (p.get("operation_id"), 1),
        }
        if kind in ids and ids[kind] != (
            event["aggregate_id"],
            event["aggregate_version"],
        ):
            raise ValueError("identity_revision_mismatch")
    except (KeyError, TypeError, FileNotFoundError) as exc:
        raise ValueError("unknown_or_malformed_contract") from exc
