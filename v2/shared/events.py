"""Contract construction only; business policy stays inside owner handlers."""

from v2.shared.contracts import new_event, stable_id


def emit(tx, cause, name, owner, kind, key, version, payload):
    e = new_event(
        name,
        owner,
        kind,
        key,
        version,
        payload,
        causation_id=cause["event_id"],
        correlation_id=cause["correlation_id"],
    )
    tx.emit(e)
    return e


def command(tx, cause, target, action, subject, arguments, actor=None):
    operation = stable_id(cause["event_id"], target, action, subject)
    return emit(
        tx,
        cause,
        target + ".command.execute",
        getattr(tx, "owner", cause["producer"]),
        "command",
        operation,
        1,
        {
            "operation_id": operation,
            "action": action,
            "subject_id": subject,
            "actor": actor or {"id": "lab-orchestrator", "roles": ["service"]},
            "expected_version": None,
            "arguments": arguments,
        },
    )


def authorize(payload, admin=False):
    roles = payload["actor"]["roles"]
    if admin and not set(roles).intersection({"admin", "service"}):
        raise ValueError("forbidden")
    subject = payload["arguments"].get("user_id", payload["subject_id"])
    if (
        not admin
        and payload["actor"]["id"] != subject
        and not set(roles).intersection({"admin", "service"})
    ):
        raise ValueError("forbidden")


def complete(tx, cause, owner, result=None):
    p = cause["payload"]
    old = tx.get("operations", p["operation_id"]) or {}
    row = {
        **old,
        "id": p["operation_id"],
        "status": "completed",
        "owner": owner,
        "subject_id": p["subject_id"],
        "result": result or {},
        "actor_id": p["actor"]["id"],
    }
    tx.put("operations", row["id"], row)
    emit(
        tx,
        cause,
        "operation.completed",
        owner,
        "domain_record",
        stable_id(row["id"]),
        1,
        {
            "operation_id": row["id"],
            "owner": owner,
            "subject_id": p["subject_id"],
            "status": "completed",
            "reason": "ok",
        },
    )
