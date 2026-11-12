from v2.shared.contracts import stable_id
from v2.shared.events import emit, command, complete, authorize


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ == "operation.completed":
        old = tx.get("operations", p["operation_id"]) or {"id": p["operation_id"]}
        old.update(status=p["status"], owner=p["owner"], subject_id=p["subject_id"])
        tx.put("operations", p["operation_id"], old)
        for saga in tx.scan("sagas"):
            if saga["status"] in ("completed", "failed"):
                continue
            states = [(tx.get("operations", key) or {}).get("status") for key in saga["operations"]]
            if "failed" in states:
                saga.update(
                    status="failed",
                    reason="downstream_operation_failed",
                    reconciliation="manual_reward_review",
                )
                suggestion = tx.get("suggestions", saga["id"])
                suggestion["status"] = "approval_failed"
                tx.put("suggestions", suggestion["id"], suggestion)
                tx.put("sagas", saga["id"], saga)
                continue
            if states and all(state == "completed" for state in states):
                suggestion = tx.get("suggestions", saga["id"])
                if saga.get("phase") == "configure":
                    operation = command(
                        tx,
                        e,
                        "listener",
                        "set_subscription",
                        suggestion["source_id"],
                        {"enabled": True, "recipe_key": saga["recipe_key"]},
                    )
                    saga.update(
                        phase="activate",
                        operations=[operation["payload"]["operation_id"]],
                    )
                else:
                    saga["status"] = "completed"
                    suggestion["status"] = "approved"
                    tx.put("suggestions", suggestion["id"], suggestion)
                tx.put("sagas", saga["id"], saga)
        return
    if typ.startswith("catalog."):
        tx.put("catalog_projection", p["canonical_id"], p)
        return
    if typ == "parser.candidate.rejected":
        tx.put(
            "quarantine_projection",
            p["batch_id"],
            {
                "id": p["batch_id"],
                "reason": p["reason_code"],
                "reference": p["quarantine_ref"],
            },
        )
        return
    if typ in ("notifications.delivery.changed", "content.publication.changed"):
        tx.put("delivery_projection", p["subject_id"], p)
        return
    if not typ.endswith(".command.execute"):
        return
    action = p["action"]
    a = p["arguments"]
    authorize(p, admin=action != "suggest_source")
    if action == "suggest_source":
        tx.put(
            "suggestions",
            p["subject_id"],
            {
                "id": p["subject_id"],
                "user_id": a.get("user_id", p["actor"]["id"]),
                "source_id": a.get("source_id", p["subject_id"]),
                "status": "pending",
            },
        )
    elif action == "approve_source":
        row = tx.get("suggestions", p["subject_id"])
        if not row:
            raise ValueError("unknown_suggestion")
        row["status"] = "activating"
        tx.put("suggestions", row["id"], row)
        sub = command(
            tx,
            e,
            "listener",
            "set_subscription",
            row["source_id"],
            {
                "enabled": False,
                "recipe_key": a.get("recipe_key", "generic.single_listing"),
            },
        )
        pol = command(
            tx,
            e,
            "source_parser",
            "set_policy",
            stable_id("policy", row["source_id"]),
            {"recipe_key": a.get("recipe_key", "generic.single_listing")},
        )
        reward = command(
            tx,
            e,
            "identity",
            "grant_premium",
            row["user_id"],
            {
                "user_id": row["user_id"],
                "suggestion_id": row["id"],
                "reason": "synthetic_source_reward",
            },
        )
        tx.put(
            "sagas",
            row["id"],
            {
                "id": row["id"],
                "operations": [
                    sub["payload"]["operation_id"],
                    pol["payload"]["operation_id"],
                    reward["payload"]["operation_id"],
                ],
                "status": "pending",
                "phase": "configure",
                "recipe_key": a.get("recipe_key", "generic.single_listing"),
            },
        )
    elif action == "set_flag":
        key = a.get("flag", p["subject_id"])
        old = tx.get("flags", key) or {"revision": 0}
        row = {
            "id": key,
            "flag": key,
            "enabled": a.get("enabled", False),
            "revision": old["revision"] + 1,
        }
        tx.put("flags", key, row)
        emit(
            tx,
            e,
            "control.flag.changed",
            "control",
            "domain_record",
            stable_id(key),
            row["revision"],
            {
                "subject_id": key,
                "revision": row["revision"],
                "flag": key,
                "enabled": row["enabled"],
            },
        )
    else:
        raise ValueError("unsupported_control_action")
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
    complete(tx, e, "control")
