"""One owner for filters, projections, due scheduling and FAKE_ONLY send ledger."""

import time
from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def match(listing, c):
    if listing["visibility"] != "active" or listing["parent_canonical_id"]:
        return False
    x = listing["listing"]
    price = x.get("price_normalized_monthly", x["price"])
    if x["price_period"] == "one_time" or (
        not c.get("include_per_person") and x["price_basis"] == "per_person"
    ):
        return False
    if c.get("min_price") is not None and (price is None or price < c["min_price"]):
        return False
    if c.get("max_price") is not None and (price is None or price > c["max_price"]):
        return False
    if c.get("rooms") is not None and x["rooms"] != c["rooms"]:
        return False
    if c.get("district") and (x["district"] or "").casefold() != c["district"].casefold():
        return False
    if c.get("audience_tag") and c["audience_tag"] not in x["audience_tags"]:
        return False
    return True


def rematch(tx):
    for f in tx.scan("filters"):
        user = tx.get("users", f["user_id"])
        if not f["enabled"] or not user or not user["active"] or not user["consent"]:
            continue
        for listing in tx.scan("listings"):
            if match(listing, f["criteria"]):
                key = stable_id("notification", f["user_id"], f["id"], listing["canonical_id"])
                if not tx.get("jobs", key):
                    tx.put(
                        "jobs",
                        key,
                        {
                            "id": key,
                            "user_id": f["user_id"],
                            "filter_id": f["id"],
                            "canonical_id": listing["canonical_id"],
                            "status": "pending",
                            "attempts": 0,
                            "due": time.time(),
                            "lease": None,
                            "lease_until": None,
                            "payload_hash": None,
                            "priority": 100 if user["premium"] else 0,
                        },
                    )


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ == "identity.user.changed":
        old = tx.get("users", p["user_id"])
        if not old or old["revision"] < p["revision"]:
            tx.put("users", p["user_id"], p)
        for job in tx.scan("jobs"):
            if job["user_id"] == p["user_id"] and job["status"] == "parked_entitlement":
                job.update(status="pending", due=time.time())
                tx.put("jobs", job["id"], job)
        rematch(tx)
        return
    if typ.startswith("catalog.listing."):
        old = tx.get("listings", p["canonical_id"])
        if not old or old["canonical_revision"] < p["canonical_revision"]:
            tx.put("listings", p["canonical_id"], p)
        rematch(tx)
        return
    if typ == "taxonomy.tag.changed":
        for f in tx.scan("filters"):
            if f["criteria"].get("audience_tag") == p["tag"] and p["replacement"]:
                f["criteria"]["audience_tag"] = p["replacement"]
                tx.put("filters", f["id"], f)
        return
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if not typ.endswith(".command.execute"):
        return
    action = p["action"]
    a = p["arguments"]
    authorize(p, admin=action in ("deliver", "resolve_unknown"))
    if action == "save_filter":
        uid = a.get("user_id", p["actor"]["id"])
        fid = a.get("filter_id", p["subject_id"])
        old = tx.get("filters", fid)
        if old and old["user_id"] != uid:
            raise ValueError("forbidden")
        criteria = a.get("criteria", {})
        if (
            criteria.get("min_price") is not None
            and criteria.get("max_price") is not None
            and criteria["min_price"] > criteria["max_price"]
        ):
            raise ValueError("invalid_range")
        f = {
            "id": fid,
            "user_id": uid,
            "name": a.get("name", "Synthetic filter"),
            "enabled": a.get("enabled", True),
            "criteria": criteria,
            "revision": (old or {}).get("revision", 0) + 1,
        }
        tx.put("filters", fid, f)
        emit(
            tx,
            e,
            "notifications.filter.activated",
            "notifications",
            "domain_record",
            stable_id(fid),
            f["revision"],
            {
                "subject_id": fid,
                "revision": f["revision"],
                "user_id": uid,
                "filter_id": fid,
            },
        )
        rematch(tx)
    elif action == "delete_filter":
        f = tx.get("filters", a.get("filter_id", p["subject_id"]))
        if f:
            if p["actor"]["id"] != f["user_id"] and not set(p["actor"]["roles"]).intersection(
                {"admin", "service"}
            ):
                raise ValueError("forbidden")
            f["enabled"] = False
            tx.put("filters", f["id"], f)
    elif action == "set_consent":
        uid = a.get("user_id", p["subject_id"])
        tx.put("consent", uid, {"id": uid, "consent": a.get("consent", False)})
        rematch(tx)
    elif action == "deliver":
        deliver(tx, e, a.get("sender_mode", "success"))
    elif action == "resolve_unknown":
        job = tx.get("jobs", p["subject_id"])
        if not job or job["status"] != "unknown" or not a.get("reason"):
            raise ValueError("invalid_resolution")
        if a.get("outcome") not in ("sent", "cancelled", "resend"):
            raise ValueError("invalid_resolution")
        tx.put(
            "resolution_audit",
            p["operation_id"],
            {
                "id": p["operation_id"],
                "actor": p["actor"]["id"],
                "reason": a["reason"],
                "job_id": job["id"],
            },
        )
        job["status"] = "pending" if a["outcome"] == "resend" else a["outcome"]
        job["due"] = time.time()
        tx.put("jobs", job["id"], job)
    else:
        raise ValueError("unsupported_notification_action")
    complete(tx, e, "notifications")


def deliver(tx, e, mode):
    if mode not in (
        "success",
        "unknown",
        "timeout",
        "rate_limit",
        "transient",
        "permanent",
    ):
        raise ValueError("fake_sender_mode")
    candidates = sorted(
        tx.scan("jobs"),
        key=lambda item: (
            -int((tx.get("users", item["user_id"]) or {}).get("premium", False)),
            item["id"],
        ),
    )
    for candidate in candidates:
        job = tx.get("jobs", candidate["id"])  # Store.get uses row lock; recheck after acquisition
        if job["status"] == "sending" and (job.get("lease_until") or 0) <= time.time():
            job["status"] = "unknown"
            tx.put("jobs", job["id"], job)
            continue
        if job["status"] not in ("pending", "retry") or job["due"] > time.time():
            continue
        user = tx.get("users", job["user_id"])
        consent = tx.get("consent", job["user_id"])
        f = tx.get("filters", job["filter_id"])
        listing = tx.get("listings", job["canonical_id"])
        if not user or (user.get("expires_at") or 0) <= time.time():
            job["status"] = "parked_entitlement"
            tx.put("jobs", job["id"], job)
            continue
        job["priority"] = 100 if user["premium"] else 0
        if (
            not user["consent"]
            or (consent and not consent["consent"])
            or not user["active"]
            or not f
            or not f["enabled"]
            or not listing
            or not match(listing, f["criteria"])
        ):
            job["status"] = "cancelled"
            tx.put("jobs", job["id"], job)
            continue
        rate = tx.get("send_rate", job["user_id"])
        if rate and rate["due"] > time.time():
            continue
        job["lease"] = stable_id(e["event_id"], job["id"])
        job["lease_until"] = time.time() + 60
        job["status"] = "sending"
        job["attempts"] += 1
        from v2.shared.contracts import canonical_hash

        job["payload_hash"] = canonical_hash(
            {
                "filter": f["name"],
                "listing": listing["listing"],
                "revision": listing["canonical_revision"],
            }
        )
        # Fake side effect only; persisted before commit in same owner transaction.
        status = (
            "sent"
            if mode == "success"
            else "unknown"
            if mode in ("timeout", "unknown")
            else "failed"
            if mode == "permanent" or job["attempts"] >= 6
            else "retry"
        )
        job["status"] = status
        if status == "retry":
            job["due"] = time.time() + [5, 15, 45, 135, 405][job["attempts"] - 1]
        if status == "sent":
            tx.put(
                "send_rate",
                job["user_id"],
                {"id": job["user_id"], "due": time.time() + 1},
            )
        attempt = stable_id(job["id"], job["attempts"])
        tx.put(
            "attempts",
            attempt,
            {
                "id": attempt,
                "job_id": job["id"],
                "attempt": job["attempts"],
                "mode": "FAKE_ONLY",
                "result": status,
                "payload_hash": job["payload_hash"],
                "premium_at_send": user["premium"],
                "user_id": job["user_id"],
            },
        )
        tx.put("jobs", job["id"], job)
        emit(
            tx,
            e,
            "notifications.delivery.changed",
            "notifications",
            "domain_record",
            job["id"],
            job["attempts"],
            {
                "subject_id": job["id"],
                "revision": job["attempts"],
                "job_id": job["id"],
                "status": status,
            },
        )
