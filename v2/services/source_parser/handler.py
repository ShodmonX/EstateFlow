"""Recipe parsing with persisted source/candidate generations and closed quarantine."""

import re
from v2.shared.contracts import stable_id, canonical_hash
from v2.shared.events import emit, complete, authorize


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if typ.endswith(".command.execute"):
        authorize(p, admin=True)
        a = p["arguments"]
        action = p["action"]
        if action == "set_policy":
            for x in (
                a.get("strip_patterns", []) + a.get("include_any", []) + a.get("exclude_any", [])
            ):
                if len(x) > 256 or re.search(r"\([^)]*[+*][^)]*\)[+*]", x):
                    raise ValueError("unsafe_regex")
                re.compile(x)
            tx.put("policies", p["subject_id"], {"id": p["subject_id"], **a})
            complete(tx, e, "source_parser")
            return
        if action in ("reparse", "release_quarantine"):
            row = tx.get("inputs", p["subject_id"])
            if not row:
                raise ValueError("unknown_input")
            if not a.get("reason"):
                raise ValueError("audit_reason_required")
            tx.put(
                "replay_audit",
                p["operation_id"],
                {
                    "id": p["operation_id"],
                    "actor": p["actor"]["id"],
                    "reason": a["reason"],
                },
            )
            parse(tx, e, row["raw"], p["subject_id"], a.get("recipe_key"))
            complete(tx, e, "source_parser")
            return
        if action == "expire_buffers":
            for row in tx.scan("buffers"):
                tx.put("quarantine", row["id"], dict(row, reason="buffer_expired"))
            complete(tx, e, "source_parser")
            return
        raise ValueError("unsupported_parser_action")
    if typ.startswith("raw.message."):
        uid = p["source"]["message_uid"]
        old = tx.get("raw", uid)
        if old and old["source_revision"] >= p["source_revision"]:
            return
        tx.put("raw", uid, p)
        if typ == "raw.message.deleted":
            return
        if p["observed_order"] != "confirmed":
            tx.put("quarantine", uid, {"id": uid, "reason": "order_uncertain"})
            return
        if p["album_id"]:
            return
        parse(tx, e, p, uid)
        return
    if typ == "source.album.changed":
        if p["assembly_state"] != "settled":
            return
        members = []
        for ref in p["members"]:
            row = tx.get("raw", ref["message_uid"])
            if not row or row["source_revision"] < ref["source_revision"]:
                raise RuntimeError("album_member_not_arrived")
            if ref["state"] == "active":
                members.append(row)
        if not members:
            return
        raw = dict(members[0])
        raw["text"] = "\n\n".join(m["text"] for m in members)
        raw["media_capture_refs"] = list(
            dict.fromkeys(x for m in members for x in m["media_capture_refs"])
        )
        raw["source_refs"] = [
            {
                "message_uid": m["source"]["message_uid"],
                "source_revision": m["source_revision"],
            }
            for m in members
        ]
        raw["album_revision"] = p["album_revision"]
        parse(tx, e, raw, p["album_id"])


def parse(tx, e, raw, input_id, override=None):
    previous = tx.get("inputs", input_id)
    generation = (previous or {}).get("generation", 0) + 1
    recipe = override or raw["policy"]["recipe_key"]
    config = tx.get("policies", raw["policy"]["policy_id"]) or {}
    policy = dict(raw["policy"])
    policy["recipe_key"] = recipe
    if override:
        policy.update(
            policy_revision=generation,
            recipe_version=str(generation),
            config_sha256=canonical_hash(config),
        )
    batch = stable_id("batch", input_id, generation, policy["config_sha256"])
    refs = raw.get("source_refs") or [
        {
            "message_uid": raw["source"]["message_uid"],
            "source_revision": raw["source_revision"],
        }
    ]
    text = raw["text"]
    for pattern in config.get("strip_patterns", []):
        text = re.sub(pattern, "", text)
    disposition = None
    if recipe == "buffered_thread" or recipe == "generic.buffered_thread":
        disposition = "buffer"
    elif recipe not in (
        "generic.single_listing",
        "generic.digest_blocks",
        "generic.album_caption",
        "generic.mixed_feed",
    ):
        disposition = "quarantine"
    elif recipe == "generic.mixed_feed" and (
        re.search(r"(?i)reklama|vacancy|реклама", text)
        or not re.search(r"(?i)rent|ijara|аренд|kvartira|квартир", text)
    ):
        disposition = "quarantine"
    parts = (
        [
            x.strip()
            for x in re.split(config.get("split_pattern", r"(?m)^\s*\d+[.)]\s+"), text)
            if x.strip()
        ]
        if recipe == "generic.digest_blocks"
        else [text.strip()]
    )
    if len(parts) > 100 or len(text.encode()) > 1048576:
        disposition = "quarantine"
    if disposition:
        row = {
            "id": input_id,
            "raw": raw,
            "reason": "buffer_unavailable" if disposition == "buffer" else "policy_rejected",
            "generation": generation,
        }
        tx.put("buffers" if disposition == "buffer" else "quarantine", input_id, row)
        tx.put(
            "inputs",
            input_id,
            {
                "id": input_id,
                "raw": raw,
                "generation": generation,
                "batch_id": (previous or {}).get("batch_id"),
                "candidate_ids": (previous or {}).get("candidate_ids", []),
            },
        )
        emit(
            tx,
            e,
            "parser.candidate.rejected",
            "source_parser",
            "parser_batch",
            batch,
            generation,
            {
                "batch_id": batch,
                "source_refs": refs,
                "policy": policy,
                "disposition": disposition,
                "reason_code": row["reason"],
                "quarantine_ref": "quarantine/" + input_id if disposition == "quarantine" else None,
            },
        )
        return
    ids = []
    for index, part in enumerate(parts):
        cid = stable_id("candidate", batch, index, canonical_hash(part))
        ids.append(cid)
        lineage = {
            "batch_id": batch,
            "candidate_id": cid,
            "candidate_version": 1,
            "slot_key": "slot-" + str(index),
            "generation": generation,
            "source_refs": refs,
            "album_id": raw.get("album_id"),
            "album_revision": raw.get("album_revision"),
            "policy": policy,
        }
        emit(
            tx,
            e,
            "parser.candidate.parsed",
            "source_parser",
            "candidate",
            cid,
            1,
            {
                "lineage": lineage,
                "text": part,
                "media_capture_refs": raw["media_capture_refs"],
                "media_required": raw.get("media_required", False),
                "supersedes_candidate_ids": (previous or {}).get("candidate_ids", []),
            },
        )
    manifest = {
        "batch_id": batch,
        "generation": generation,
        "source_refs": refs,
        "policy": policy,
        "candidate_ids": ids,
        "supersedes_batch_id": (previous or {}).get("batch_id"),
        "retracts_candidate_ids": (previous or {}).get("candidate_ids", []),
        "outcome": "complete",
        "input_id": input_id,
    }
    tx.put(
        "inputs",
        input_id,
        {
            "id": input_id,
            "raw": raw,
            "generation": generation,
            "batch_id": batch,
            "candidate_ids": ids,
        },
    )
    emit(
        tx,
        e,
        "parser.batch.completed",
        "source_parser",
        "parser_batch",
        batch,
        generation,
        manifest,
    )
