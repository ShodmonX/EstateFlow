import time
from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ.startswith("catalog."):
        old = tx.get("listings", p["canonical_id"])
        if not old or old["canonical_revision"] < p["canonical_revision"]:
            tx.put("listings", p["canonical_id"], p)
        return
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if not typ.endswith(".command.execute"):
        return
    authorize(p, admin=True)
    a = p["arguments"]
    key = p["subject_id"]
    row = tx.get("posts", key)
    if p["action"] == "schedule":
        if not row:
            row = {
                "id": key,
                "status": "pending",
                "revision": 1,
                "due": time.time(),
                "attempts": 0,
                "kind": a.get("kind", "top_offer"),
            }
            tx.put("posts", key, row)
    elif p["action"] == "publish":
        if not row:
            raise ValueError("post_not_scheduled")
        if row["status"] in ("unknown", "sent", "cancelled", "failed") or row["due"] > time.time():
            complete(tx, e, "content")
            return
        visible = [r for r in tx.scan("listings") if r["visibility"] == "active"]
        row["rendered_count"] = len(visible)
        row["text"] = "SYNTHETIC offers: " + str(len(visible))
        row["attempts"] += 1
        mode = a.get("sender_mode", "success")
        if mode not in (
            "success",
            "unknown",
            "timeout",
            "rate_limit",
            "transient",
            "permanent",
        ):
            raise ValueError("fake_sender_mode")
        row["status"] = (
            "sent"
            if mode == "success"
            else "unknown"
            if mode in ("unknown", "timeout")
            else "failed"
            if mode == "permanent" or row["attempts"] >= 6
            else "retry"
        )
        row["revision"] += 1
        row["mode"] = "FAKE_ONLY"
        if row["status"] == "retry":
            row["due"] = time.time() + [5, 15, 45, 135, 405][row["attempts"] - 1]
        tx.put("posts", key, row)
        tx.put(
            "attempts",
            stable_id(key, row["attempts"]),
            {
                "id": stable_id(key, row["attempts"]),
                "post_id": key,
                "status": row["status"],
                "mode": "FAKE_ONLY",
            },
        )
        emit(
            tx,
            e,
            "content.publication.changed",
            "content",
            "domain_record",
            stable_id(key),
            row["revision"],
            {
                "subject_id": key,
                "revision": row["revision"],
                "post_id": key,
                "status": row["status"],
            },
        )
    elif p["action"] == "resolve_unknown":
        if not row or row["status"] != "unknown" or not a.get("reason"):
            raise ValueError("invalid_resolution")
        if a.get("outcome") not in ("sent", "cancelled", "resend"):
            raise ValueError("invalid_resolution")
        row["status"] = "pending" if a["outcome"] == "resend" else a["outcome"]
        row["due"] = time.time()
        tx.put("posts", key, row)
        tx.put(
            "audit",
            p["operation_id"],
            {"id": p["operation_id"], "actor": p["actor"]["id"], "reason": a["reason"]},
        )
    else:
        raise ValueError("unsupported_content_action")
    complete(tx, e, "content")
