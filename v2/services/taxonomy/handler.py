from v2.shared.contracts import stable_id
from v2.shared.events import emit, complete, authorize


def handle(tx, e):
    p = e["payload"]
    if e["event_type"] == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if not e["event_type"].endswith(".command.execute"):
        return
    authorize(p, admin=True)
    a = p["arguments"]
    tag = a.get("tag", p["subject_id"])
    old = tx.get("tags", tag) or {"revision": 0}
    if p["action"] not in ("approve_tag", "remap_tag"):
        raise ValueError("unsupported_taxonomy_action")
    replacement = a.get("replacement") if p["action"] == "remap_tag" else None
    if replacement == tag:
        raise ValueError("self_remap")
    row = {
        "id": tag,
        "tag": tag,
        "revision": old["revision"] + 1,
        "replacement": replacement,
        "approved": True,
    }
    tx.put("tags", tag, row)
    emit(
        tx,
        e,
        "taxonomy.tag.changed",
        "taxonomy",
        "domain_record",
        stable_id(tag),
        row["revision"],
        {
            "subject_id": tag,
            "revision": row["revision"],
            "tag": tag,
            "replacement": replacement,
            "approved": True,
        },
    )
    complete(tx, e, "taxonomy")
