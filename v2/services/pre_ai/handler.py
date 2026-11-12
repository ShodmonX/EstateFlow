import re
from difflib import SequenceMatcher
from v2.shared.contracts import canonical_hash
from v2.shared.events import emit, complete


def handle(tx, e):
    p = e["payload"]
    if e["event_type"].endswith(".command.execute"):
        complete(tx, e, "pre_ai")
        return
    if e["event_type"] == "control.flag.changed":
        tx.put("flags", p["flag"], p)
        return
    if e["event_type"].startswith("catalog.listing."):
        for signal in tx.scan("signals"):
            evidence = [
                x for x in p["source_evidence"] if x["candidate_id"] == signal["candidate_id"]
            ]
            if evidence or signal.get("canonical_id") == p["canonical_id"]:
                signal["confirmed"] = p["visibility"] == "active" and any(
                    x["state"] == "active" for x in evidence
                )
                signal["canonical_id"] = p["canonical_id"]
                tx.put("signals", signal["id"], signal)
        return
    if e["event_type"] != "parser.candidate.parsed":
        return
    line = p["lineage"]
    cid = line["candidate_id"]
    text = re.sub(r"\s+", " ", p["text"].casefold()).strip()
    key = canonical_hash(text)
    old = tx.get("signals", key)
    source = line["source_refs"][0]["message_uid"]
    duplicate = bool(
        old and old.get("confirmed", False) and old["source"] != source and line["generation"] == 1
    )
    near = max(
        (SequenceMatcher(None, text, s["text"]).ratio() for s in tx.scan("signals")),
        default=0,
    )
    tx.put(
        "decisions",
        cid,
        {
            "id": cid,
            "duplicate": duplicate,
            "near_text": near,
            "signal": "normalized_text_sha256",
            "generation": line["generation"],
        },
    )
    if not old or not old.get("confirmed", False):
        tx.put(
            "signals",
            key,
            {
                "id": key,
                "candidate_id": cid,
                "source": source,
                "text": text,
                "confirmed": False,
            },
        )
    emit(
        tx,
        e,
        "candidate.prechecked",
        "pre_ai",
        "candidate",
        cid,
        1,
        {
            "lineage": line,
            "decision": "duplicate" if duplicate else "accepted",
            "duplicate_of_candidate_id": old["candidate_id"] if duplicate else None,
            "signal_revision": 1,
            "reason_code": "exact_text" if duplicate else "accepted",
            "parsed_event_id": e["event_id"],
            "text": p["text"],
            "media_capture_refs": p["media_capture_refs"],
            "media_required": p.get("media_required", False),
        },
    )
