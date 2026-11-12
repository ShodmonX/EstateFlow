"""Synthetic transport journal. No Telegram client/session code exists here."""

import time
from v2.shared.contracts import stable_id, canonical_hash
from v2.shared.events import emit, complete, authorize


def policy(source, recipe="generic.single_listing"):
    return {
        "policy_id": stable_id("policy", source),
        "policy_revision": 1,
        "recipe_key": recipe,
        "recipe_version": "1",
        "config_sha256": canonical_hash({"recipe": recipe}),
        "snapshot_ref": "policy/synthetic/" + source,
    }


def handle(tx, e):
    if e["event_type"] == "control.flag.changed":
        tx.put("flags", e["payload"]["flag"], e["payload"])
        return
    if not e["event_type"].endswith(".command.execute"):
        return
    p = e["payload"]
    a = p["arguments"]
    authorize(p, admin=True)
    if p["action"] == "set_subscription":
        row = {
            "id": p["subject_id"],
            "enabled": a.get("enabled", True),
            "policy": policy(p["subject_id"], a.get("recipe_key", "generic.single_listing")),
        }
        tx.put("subscriptions", row["id"], row)
        complete(tx, e, "listener", row)
        return
    if p["action"] == "reconcile":
        row = tx.get("uncertain", p["subject_id"])
        if not row:
            raise ValueError("unknown_observation")
        if not a.get("approved"):
            raise ValueError("reconciliation_requires_approval")
        tx.put(
            "reconciliations",
            p["subject_id"],
            {
                "id": p["subject_id"],
                "actor": p["actor"]["id"],
                "reason": a.get("reason", "synthetic"),
            },
        )
        a = row["arguments"]
        a["observed_order"] = "confirmed"
    if p["action"] == "flush_albums":
        for row in tx.scan("albums"):
            if row["state"] == "settling" and (time.time() >= row["due"] or a.get("approved")):
                row["state"] = "settled"
                tx.put("albums", row["id"], row)
                album_event(tx, e, row)
        complete(tx, e, "listener")
        return
    if p["action"] not in ("ingest", "reconcile"):
        raise ValueError("unsupported_listener_action")
    source = a.get("source_id", "synthetic-source")
    channel = a.get("channel_id", "synthetic-channel")
    native = a.get("message_id", p["subject_id"])
    key = stable_id("message", source, channel, native)
    old = tx.get("messages", key)
    kind = a.get("update_type", "received")
    text = a.get("text", "")
    captures = a.get("media_capture_refs", [])
    if kind not in ("received", "edited", "deleted"):
        raise ValueError("unsupported_update")
    if a.get("observed_order", "confirmed") != "confirmed":
        tx.put(
            "uncertain",
            p["subject_id"],
            {"id": p["subject_id"], "arguments": a, "reason": "order_uncertain"},
        )
        complete(tx, e, "listener", {"state": "parked"})
        return
    if old and old["state"] == "deleted":
        complete(tx, e, "listener", {"state": "tombstoned"})
        return
    version = a.get("source_revision") or ((old["revision"] + 1) if old else 1)
    if old and version <= old["revision"]:
        complete(tx, e, "listener", {"state": "stale"})
        return
    if old and version != old["revision"] + 1:
        tx.put(
            "uncertain",
            p["subject_id"],
            {"id": p["subject_id"], "arguments": a, "reason": "revision_gap"},
        )
        return
    sub = tx.get("subscriptions", source)
    if sub and not sub["enabled"]:
        raise ValueError("subscription_disabled")
    bind = sub["policy"] if sub else policy(source)
    album = (
        stable_id("album", source, channel, a["native_group_id"])
        if a.get("native_group_id")
        else (old or {}).get("album_id")
    )
    row = {
        "id": key,
        "revision": version,
        "state": kind,
        "source": {
            "source_id": stable_id("source", source),
            "platform": "telegram",
            "channel_id": channel,
            "message_id": native,
            "message_uid": key,
        },
        "album_id": album,
        "text": text,
        "media_capture_refs": captures,
        "policy": bind,
    }
    tx.put("messages", key, row)
    pl = {
        "source": row["source"],
        "source_revision": version,
        "observed_order": "confirmed",
        "platform_revision_token": None,
        "album_id": album,
        "policy": bind,
    }
    if kind == "deleted":
        pl["reason"] = "platform_delete"
    else:
        pl.update(
            text=text,
            media_capture_refs=captures,
            media_required=a.get("media_required", False),
            content_sha256=canonical_hash({"text": text, "refs": captures}),
        )
    emit(tx, e, "raw.message." + kind, "listener", "source_message", key, version, pl)
    if album:
        group = tx.get("albums", album) or {
            "id": album,
            "revision": 0,
            "members": {},
            "created": time.time(),
            "bytes": 0,
        }
        group["members"][key] = {
            "message_uid": key,
            "source_revision": version,
            "state": "retracted" if kind == "deleted" else "active",
        }
        group["revision"] += 1
        group["bytes"] += len(text.encode())
        if len(group["members"]) > 100 or group["bytes"] > 1048576:
            group["state"] = "quarantine"
            tx.put("quarantine", album, {"id": album, "reason": "album_limit"})
        else:
            group.update(state="settling", due=min(time.time() + 3, group["created"] + 60))
        tx.put("albums", album, group)
    complete(tx, e, "listener", {"message_uid": key, "source_revision": version})


def album_event(tx, e, row):
    emit(
        tx,
        e,
        "source.album.changed",
        "listener",
        "album",
        row["id"],
        row["revision"],
        {
            "album_id": row["id"],
            "album_revision": row["revision"],
            "members": list(row["members"].values()),
            "assembly_state": "settled",
            "change_reason": "initial" if row["revision"] == 1 else "late_member",
        },
    )
