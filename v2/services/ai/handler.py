"""Offline extraction/media orchestration with durable runs and explicit retry state."""

import base64
from pathlib import Path
from typing import Any
from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def normalize(listing):
    row = dict(listing)
    price = row["price"]
    period = row["price_period"]
    row["price_normalized_monthly"] = (
        None
        if price is None or period == "one_time"
        else price * 30
        if period == "daily"
        else price
    )
    row["listing_type"] = (
        "sale" if period == "one_time" else "rent" if period in ("daily", "monthly") else None
    )
    return row


def prepare_media(tx, cid, references):
    from .media import MediaCodec

    result: dict[str, Any] = {"state": "unavailable", "assets": [], "failure_code": None}
    for ref in references:
        if ref != "offline://fixtures/image.png":
            result = {"state": "failed", "assets": [], "failure_code": "capture_unavailable"}
            continue
        try:
            image = MediaCodec().process(Path("v2/fixtures/image.png").read_bytes())
            key = "synthetic/" + cid + "/" + image["content_sha256"] + ".png"
            tx.put(
                "objects",
                key,
                {
                    "id": key,
                    "content": base64.b64encode(image["content"]).decode(),
                    "mime_type": image["mime_type"],
                },
            )
            result = {
                "state": "ready",
                "assets": [
                    {
                        "asset_id": stable_id(key),
                        "object_key": key,
                        "content_sha256": image["content_sha256"],
                        "mime_type": image["mime_type"],
                        "width": image["width"],
                        "height": image["height"],
                    }
                ],
                "failure_code": None,
            }
        except (ValueError, OSError):
            result = {"state": "failed", "assets": [], "failure_code": "media_decode_failed"}
    return result


def publish_media(tx, e, cid, line, media):
    key = stable_id("media-job", cid)
    old = tx.get("media_jobs", key)
    version = (old or {}).get("revision", 0) + 1
    tx.put(
        "media_jobs",
        key,
        {"id": key, "candidate_id": cid, "revision": version, "state": media["state"]},
    )
    emit(
        tx,
        e,
        "media." + media["state"],
        "ai",
        "media_job",
        key,
        version,
        {
            "media_job_id": key,
            "candidate_id": cid,
            "source_revision": line["source_refs"][0]["source_revision"],
            "media_revision": version,
            "media": media,
        },
    )


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ == "taxonomy.tag.changed":
        tx.put("taxonomy", p["tag"], p)
        return
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    from .providers import (
        FakeExtractionProvider,
        ProviderInvalidResponse,
        ProviderRateLimit,
        ProviderTimeout,
    )

    if typ.endswith(".command.execute"):
        authorize(p, admin=True)
        a = p["arguments"]
        if p["action"] == "media_retry":
            original = tx.get("inputs", p["subject_id"])
            if not original:
                raise ValueError("unknown_media_candidate")
            if not a.get("reason"):
                raise ValueError("reason_required")
            media = prepare_media(
                tx, p["subject_id"], a.get("media_capture_refs", original["media_capture_refs"])
            )
            publish_media(tx, e, p["subject_id"], original["lineage"], media)
            tx.put(
                "media_audit",
                p["operation_id"],
                {
                    "id": p["operation_id"],
                    "actor": p["actor"]["id"],
                    "reason": a["reason"],
                    "state": media["state"],
                },
            )
            complete(tx, e, "ai", {"state": media["state"]})
            return
        if p["action"] != "extract":
            raise ValueError("unsupported_ai_action")
        try:
            listing = normalize(
                FakeExtractionProvider().extract(a["text"], mode=a.get("mode", "success"))
            )
            row = {
                "id": p["subject_id"],
                "status": "succeeded",
                "listing": listing,
                "provider": "FAKE_ONLY",
                "cost_units": 0,
            }
        except ProviderInvalidResponse:
            row = {
                "id": p["subject_id"],
                "status": "failed",
                "reason": "invalid_provider_reply",
                "provider": "FAKE_ONLY",
                "cost_units": 0,
            }
        except (ProviderRateLimit, ProviderTimeout) as exc:
            raise TimeoutError("provider_retryable") from exc
        tx.put("runs", p["subject_id"], row)
        complete(tx, e, "ai", row)
        return
    if typ != "candidate.prechecked" or p["decision"] != "accepted":
        return
    line = p["lineage"]
    cid = line["candidate_id"]
    existing = tx.get("runs", cid)
    if existing and existing["status"] == "succeeded":
        return
    tx.put("inputs", cid, p)
    try:
        listing = normalize(FakeExtractionProvider().extract(p["text"]))
    except (ProviderRateLimit, ProviderTimeout) as exc:
        raise TimeoutError("provider_retryable") from exc
    except ProviderInvalidResponse:
        tx.put(
            "runs",
            cid,
            {
                "id": cid,
                "status": "failed",
                "reason": "invalid_provider_reply",
                "provider": "FAKE_ONLY",
            },
        )
        return
    media = prepare_media(tx, cid, p["media_capture_refs"])
    tx.put(
        "runs",
        cid,
        {
            "id": cid,
            "status": "succeeded",
            "listing": listing,
            "source_refs": line["source_refs"],
            "provider": "FAKE_ONLY",
            "cost_units": 0,
            "prompt_version": "lab.synthetic.v1",
        },
    )
    acceptable = (
        (not p.get("media_required", False) or media["state"] == "ready")
        and listing.get("confidence", 1) >= 0.6
        and (listing["price"] is not None or listing["rooms"] is not None or listing["district"])
    )
    emit(
        tx,
        e,
        "candidate.extracted",
        "ai",
        "candidate",
        cid,
        1,
        {
            "lineage": line,
            "extraction_run_id": stable_id("run", cid),
            "prompt_version": "lab.synthetic.v1",
            "outcome": "accepted" if acceptable else "review_required",
            "listing": listing,
            "media": media,
        },
    )
    publish_media(tx, e, cid, line, media)
