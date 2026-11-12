"""Authoritative source-evidence aggregate. All mutations use owner transaction/outbox."""

from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def publish(tx, e, row, reason):
    row.setdefault("created_at", e["occurred_at"])
    row["updated_at"] = e["recorded_at"]
    row["canonical_revision"] += 1
    row["mutation_id"] = stable_id(e["event_id"], row["canonical_id"], row["canonical_revision"])
    row["reason"] = reason
    tx.put("listings", row["canonical_id"], row)
    name = (
        "catalog.listing.retracted"
        if row["visibility"] in ("hidden", "merged")
        else "catalog.listing.changed"
    )
    snapshot = emit(
        tx,
        e,
        name,
        "catalog",
        "canonical_listing",
        row["canonical_id"],
        row["canonical_revision"],
        row,
    )
    tx.put(
        "snapshots",
        snapshot["event_id"],
        {"id": snapshot["event_id"], "event": snapshot},
    )


def handle(tx, e):
    if hasattr(tx, "lock"):
        tx.lock("catalog:mutation")
    p = e["payload"]
    typ = e["event_type"]
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if typ == "taxonomy.tag.changed":
        if p["replacement"]:
            for row in tx.scan("listings"):
                tags = row["listing"]["audience_tags"]
                if p["tag"] in tags:
                    row["listing"]["audience_tags"] = list(
                        dict.fromkeys(p["replacement"] if x == p["tag"] else x for x in tags)
                    )
                    publish(tx, e, row, "taxonomy_changed")
        return
    if typ.endswith(".command.execute"):
        authorize(p, admin=True)
        a = p["arguments"]
        if p["action"] == "review_batch":
            batch = tx.get("pending_batches", p["subject_id"])
            if not batch or batch["status"] != "pending" or batch["new_ids"]:
                raise ValueError("invalid_empty_batch_review")
            watermark = tx.get("input_watermarks", batch["input_id"])
            if watermark and watermark["batch_id"] != batch["id"]:
                raise ValueError("superseded_review")
            if a["approved"]:
                finalize_batch(tx, e, batch)
            else:
                batch["status"] = "rejected"
            tx.put("pending_batches", batch["id"], batch)
            tx.put(
                "audit",
                p["operation_id"],
                {
                    "id": p["operation_id"],
                    "action": "review_batch",
                    "actor": p["actor"]["id"],
                    "reason": a["reason"],
                },
            )
            complete(tx, e, "catalog")
            return
        row = tx.get("listings", a.get("canonical_id", p["subject_id"]))
        if not row:
            raise ValueError("unknown_canonical")
        if p["expected_version"] is not None and p["expected_version"] != row["canonical_revision"]:
            raise ValueError("revision_conflict")
        action = p["action"]
        if action == "review":
            if not review_batch(tx, e, row, a["approved"]):
                row["review"] = "approved" if a["approved"] else "rejected"
                row["visibility"] = (
                    "active"
                    if a["approved"] and any(x["state"] == "active" for x in row["source_evidence"])
                    else "hidden"
                )
                publish(tx, e, row, "review_approved" if a["approved"] else "review_rejected")
        elif action == "merge":
            target = tx.get("listings", a["target_id"])
            if (
                not target
                or target["visibility"] == "merged"
                or target["canonical_id"] == row["canonical_id"]
            ):
                raise ValueError("invalid_merge_target")
            target["source_evidence"] += row["source_evidence"]
            target["redirect_from_ids"] = list(
                dict.fromkeys(target["redirect_from_ids"] + [row["canonical_id"]])
            )
            for ev in row["source_evidence"]:
                tx.put(
                    "candidate_map",
                    ev["candidate_id"],
                    {"id": ev["candidate_id"], "canonical_id": target["canonical_id"]},
                )
            row.update(
                parent_canonical_id=target["canonical_id"],
                visibility="merged",
                related_canonical_ids=[target["canonical_id"]],
            )
            publish(tx, e, row, "merged")
            target["related_canonical_ids"] = [row["canonical_id"]]
            publish(tx, e, target, "merged")
        elif action == "retract":
            row["visibility"] = "hidden"
            publish(tx, e, row, "evidence_retracted")
        elif action == "reinstate":
            if not a.get("reason"):
                raise ValueError("reason_required")
            row["visibility"] = "review"
            row["review"] = "pending"
            publish(tx, e, row, "edited")
        else:
            raise ValueError("unsupported_catalog_action")
        tx.put(
            "audit",
            p["operation_id"],
            {
                "id": p["operation_id"],
                "action": action,
                "actor": p["actor"]["id"],
                "reason": a.get("reason", "synthetic"),
            },
        )
        complete(tx, e, "catalog")
        return
    if typ == "raw.message.deleted":
        uid = p["source"]["message_uid"]
        old = tx.get("tombstones", uid)
        if old and old["source_revision"] >= p["source_revision"]:
            return
        tx.put("tombstones", uid, {"id": uid, "source_revision": p["source_revision"]})
        for row in tx.scan("listings"):
            changed = False
            for ev in row["source_evidence"]:
                if ev["message_uid"] == uid and ev["source_revision"] <= p["source_revision"]:
                    ev.update(state="retracted", source_revision=p["source_revision"])
                    changed = True
            if changed:
                if not any(x["state"] == "active" for x in row["source_evidence"]):
                    row["visibility"] = "hidden"
                publish(tx, e, row, "evidence_retracted")
        return
    if typ == "parser.batch.completed":
        tx.put("manifests", p["batch_id"], p)
        activate_batch(tx, e, p["batch_id"])
        return
    if typ == "candidate.prechecked":
        if p["decision"] == "duplicate":
            tx.put("pending_duplicates", p["lineage"]["candidate_id"], p)
            attach_duplicates(tx, e)
            activate_batch(tx, e, p["lineage"]["batch_id"])
        return
    if typ.startswith("media."):
        old_media = tx.get("media_projection", p["candidate_id"])
        if old_media and old_media["media_revision"] >= p["media_revision"]:
            return
        tx.put("media_projection", p["candidate_id"], p)
        mapping = tx.get("candidate_map", p["candidate_id"])
        if not mapping:
            return
        row = tx.get("listings", mapping["canonical_id"])
        if row["visibility"] in ("hidden", "merged"):
            return
        if not any(
            x["candidate_id"] == p["candidate_id"]
            and x["state"] == "active"
            and x["source_revision"] == p["source_revision"]
            for x in row["source_evidence"]
        ):
            return
        if row["media"] != p["media"]:
            row["media"] = p["media"]
            publish(tx, e, row, "media_changed")
        return
    if typ != "candidate.extracted":
        return
    line = p["lineage"]
    cid = line["candidate_id"]
    if tx.get("candidate_map", cid):
        return
    tx.put("extractions", cid, p)
    activate_batch(tx, e, line["batch_id"])


def activate_batch(tx, e, batch_id):
    manifest = tx.get("manifests", batch_id)
    if not manifest or manifest["outcome"] != "complete" or tx.get("activated", batch_id):
        return
    candidates = [tx.get("extractions", cid) for cid in manifest["candidate_ids"]]
    if any(
        item is None and not tx.get("candidate_map", cid)
        for cid, item in zip(manifest["candidate_ids"], candidates)
    ):
        return
    input_id = (
        manifest.get("input_id")
        or next(
            (p["lineage"]["album_id"] for p in candidates if p and p["lineage"]["album_id"]), None
        )
        or manifest["source_refs"][0]["message_uid"]
    )
    watermark = tx.get("input_watermarks", input_id)
    refs = {x["message_uid"]: x["source_revision"] for x in manifest["source_refs"]}
    if watermark and (
        manifest["generation"] < watermark["generation"]
        or any(
            refs.get(uid, 0) < revision
            for uid, revision in watermark["source_refs"].items()
            if uid in refs
        )
    ):
        tx.put("activated", batch_id, {"id": batch_id, "status": "stale"})
        return
    if (
        watermark
        and manifest["generation"] == watermark["generation"]
        and batch_id != watermark["batch_id"]
    ):
        tx.put("batch_conflicts", batch_id, {"id": batch_id, "reason": "generation_conflict"})
        return
    current = tx.get("active_inputs", input_id) or {"canonical_ids": []}
    previous = [tx.get("candidate_map", cid) for cid in manifest["retracts_candidate_ids"]]
    previous_ids = list(
        dict.fromkeys(current["canonical_ids"] + [x["canonical_id"] for x in previous if x])
    )
    needs_review = (
        any((tx.get("listings", key) or {}).get("visibility") == "review" for key in previous_ids)
        or len(previous_ids) > 1
        or bool(previous_ids and len(candidates) != 1)
        or any(p and p["outcome"] == "review_required" for p in candidates)
    )
    state = {
        "id": input_id,
        "generation": manifest["generation"],
        "batch_id": batch_id,
        "source_refs": refs,
        "canonical_ids": [],
    }
    new_ids = []
    for extracted in candidates:
        if extracted is None:
            continue
        line = extracted["lineage"]
        cid = line["candidate_id"]
        if any(
            (tx.get("tombstones", r["message_uid"]) or {}).get("source_revision", 0)
            >= r["source_revision"]
            for r in line["source_refs"]
        ):
            continue
        canonical = (
            previous_ids[0] if previous_ids and not needs_review else stable_id("canonical", cid)
        )
        row = tx.get("listings", canonical)
        reason = "edited" if row else "created"
        if row is None:
            row = {
                "canonical_id": canonical,
                "canonical_revision": 0,
                "mutation_id": stable_id(e["event_id"]),
                "reason": reason,
                "visibility": "active",
                "parent_canonical_id": None,
                "source_evidence": [],
                "listing": extracted["listing"],
                "media": extracted["media"],
                "review": "none",
                "redirect_from_ids": [],
                "related_canonical_ids": [],
            }
        if not needs_review:
            row["source_evidence"] = [
                x
                for x in row["source_evidence"]
                if x["candidate_id"] not in manifest["retracts_candidate_ids"]
                and x["message_uid"] not in refs
            ]
        row["source_evidence"] += [
            {
                "message_uid": r["message_uid"],
                "source_revision": r["source_revision"],
                "state": "active",
                "candidate_id": cid,
            }
            for r in line["source_refs"]
        ]
        row["listing"] = extracted["listing"]
        row["media"] = extracted["media"]
        latest_media = tx.get("media_projection", cid)
        if latest_media and any(
            ref["source_revision"] == latest_media["source_revision"] for ref in line["source_refs"]
        ):
            row["media"] = latest_media["media"]
        if needs_review:
            row.update(visibility="review", review="pending")
        tx.put("candidate_map", cid, {"id": cid, "canonical_id": canonical})
        publish(tx, e, row, reason)
        new_ids.append(canonical)
    for cid, item in zip(manifest["candidate_ids"], candidates):
        if item is None:
            mapping = tx.get("candidate_map", cid)
            if mapping:
                new_ids.append(mapping["canonical_id"])
    state["canonical_ids"] = list(dict.fromkeys(new_ids))
    tx.put("input_watermarks", input_id, state)
    if needs_review:
        pending = {
            "id": batch_id,
            "input_id": input_id,
            "state": state,
            "old_ids": previous_ids,
            "old_candidates": manifest["retracts_candidate_ids"],
            "new_ids": state["canonical_ids"],
            "decisions": {},
            "status": "pending",
        }
        tx.put("pending_batches", batch_id, pending)
        for canonical in new_ids:
            tx.put("review_links", canonical, {"id": canonical, "batch_id": batch_id})
    else:
        tx.put("active_inputs", input_id, state)
    tx.put(
        "activated", batch_id, {"id": batch_id, "status": "review" if needs_review else "active"}
    )
    attach_duplicates(tx, e)


def review_batch(tx, e, row, approved):
    link = tx.get("review_links", row["canonical_id"])
    if not link:
        return False
    batch = tx.get("pending_batches", link["batch_id"])
    if batch["status"] != "pending":
        if row["review"] == "pending":
            return False
        raise ValueError("batch_review_closed")
    watermark = tx.get("input_watermarks", batch["input_id"])
    if watermark and watermark["generation"] > batch["state"]["generation"]:
        raise ValueError("superseded_review")
    batch["decisions"][row["canonical_id"]] = "approved" if approved else "rejected"
    row["review"] = "approved" if approved else "rejected"
    row["visibility"] = "review" if approved else "hidden"
    publish(tx, e, row, "review_approved" if approved else "review_rejected")
    if not approved:
        batch["status"] = "rejected"
    elif all(batch["decisions"].get(key) == "approved" for key in batch["new_ids"]):
        finalize_batch(tx, e, batch)
    tx.put("pending_batches", batch["id"], batch)
    return True


def finalize_batch(tx, e, batch):
    for old_id in sorted(batch["old_ids"]):
        if old_id in batch["new_ids"]:
            continue
        old = tx.get("listings", old_id)
        if not old:
            continue
        for evidence in old["source_evidence"]:
            if (
                evidence["candidate_id"] in batch["old_candidates"]
                or evidence["message_uid"] in batch["state"]["source_refs"]
            ):
                evidence["state"] = "retracted"
        if not any(x["state"] == "active" for x in old["source_evidence"]):
            old["visibility"] = "hidden"
        publish(tx, e, old, "evidence_retracted")
    for new_id in sorted(batch["new_ids"]):
        new = tx.get("listings", new_id)
        new["visibility"] = (
            "active" if any(x["state"] == "active" for x in new["source_evidence"]) else "hidden"
        )
        new["review"] = "approved"
        publish(tx, e, new, "review_approved")
    tx.put("active_inputs", batch["input_id"], batch["state"])
    batch["status"] = "completed"


def attach_duplicates(tx, e):
    for p in tx.scan("pending_duplicates"):
        cid = p["lineage"]["candidate_id"]
        if tx.get("candidate_map", cid):
            continue
        target = tx.get("candidate_map", p["duplicate_of_candidate_id"])
        if not target:
            continue
        row = tx.get("listings", target["canonical_id"])
        for ref in p["lineage"]["source_refs"]:
            tomb = tx.get("tombstones", ref["message_uid"])
            row["source_evidence"].append(
                {
                    "message_uid": ref["message_uid"],
                    "source_revision": ref["source_revision"],
                    "state": "retracted"
                    if tomb and tomb["source_revision"] >= ref["source_revision"]
                    else "active",
                    "candidate_id": cid,
                }
            )
        if (
            row["visibility"] == "hidden"
            and row["reason"] == "evidence_retracted"
            and any(x["state"] == "active" for x in row["source_evidence"])
        ):
            row["visibility"] = "active"
        tx.put("candidate_map", cid, {"id": cid, "canonical_id": target["canonical_id"]})
        publish(tx, e, row, "edited")
