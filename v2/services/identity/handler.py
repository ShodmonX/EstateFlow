import time
from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def changed(tx, e, row):
    row["revision"] += 1
    row["expires_at"] = time.time() + 300
    tx.put("users", row["user_id"], row)
    emit(
        tx,
        e,
        "identity.user.changed",
        "identity",
        "domain_record",
        stable_id(row["user_id"]),
        row["revision"],
        {
            "subject_id": row["user_id"],
            "revision": row["revision"],
            "user_id": row["user_id"],
            "active": row["active"],
            "consent": row["consent"],
            "premium": row["premium"],
            "expires_at": row["expires_at"],
        },
    )


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if typ == "notifications.filter.activated":
        key = stable_id("activation", p["user_id"])
        activation = tx.get("activations", key)
        if activation:
            return
        tx.put("activations", key, {"id": key, "user_id": p["user_id"]})
        row = tx.get("users", p["user_id"])
        if row and row.get("referrer_id"):
            ref = tx.get("users", row["referrer_id"])
            if ref:
                ref["referrals"] += 1
                ref["premium"] = ref["referrals"] >= 3 or ref["premium"]
                changed(tx, e, ref)
        return
    if not typ.endswith(".command.execute"):
        return
    authorize(p, admin=p["action"] == "grant_premium")
    a = p["arguments"]
    uid = a.get("user_id", p["subject_id"])
    row = tx.get("users", uid) or {
        "user_id": uid,
        "id": uid,
        "revision": 0,
        "active": True,
        "consent": False,
        "premium": False,
        "referrals": 0,
        "referrer_id": None,
    }
    if p["expected_version"] is not None and p["expected_version"] != row["revision"]:
        raise ValueError("revision_conflict")
    if p["action"] == "upsert_user":
        row["consent"] = a.get("consent", row["consent"])
        row["referrer_id"] = a.get("referrer_id", row["referrer_id"])
        if row["referrer_id"] == uid:
            raise ValueError("self_referral")
    elif p["action"] == "grant_premium":
        award = a.get("suggestion_id", p["operation_id"])
        if tx.get("awards", award):
            complete(tx, e, "identity")
            return
        row["premium"] = True
        tx.put(
            "awards",
            award,
            {"id": award, "user_id": uid, "reason": a.get("reason", "synthetic")},
        )
    elif p["action"] == "activate_referral":
        key = stable_id("referral", uid)
        if not tx.get("referrals", key):
            tx.put(
                "referrals",
                key,
                {"id": key, "user_id": uid, "referrer_id": a.get("referrer_id")},
            )
    else:
        raise ValueError("unsupported_identity_action")
    changed(tx, e, row)
    complete(tx, e, "identity", {"user_id": uid, "premium": row["premium"]})
