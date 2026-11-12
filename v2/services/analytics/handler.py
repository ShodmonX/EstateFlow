from v2.shared.events import complete, authorize


def handle(tx, e):
    p = e["payload"]
    typ = e["event_type"]
    if typ.endswith(".command.execute"):
        authorize(p, admin=True)
        metric = p["arguments"].get("kind", "synthetic.operator.recorded")
        row = tx.get("metrics", metric) or {"id": metric, "count": 0}
        row["count"] += 1
        tx.put("metrics", metric, row)
        complete(tx, e, "analytics", row)
        return
    if tx.get("observed", e["event_id"]):
        return
    tx.put(
        "observed",
        e["event_id"],
        {"id": e["event_id"], "event_type": typ, "producer": e["producer"]},
    )
    count = tx.get("metrics", typ) or {"id": typ, "count": 0}
    count["count"] += 1
    tx.put("metrics", typ, count)
