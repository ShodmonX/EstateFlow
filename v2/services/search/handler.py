from v2.shared.events import complete


def handle(tx, e):
    if hasattr(tx, "lock"):
        tx.lock("search:projection_mutation")
    p = e["payload"]
    typ = e["event_type"]
    if typ.startswith("catalog.listing."):
        tx.put("event_archive", e["event_id"], {"id": e["event_id"], "event": e})
        old = tx.get("listings", p["canonical_id"])
        if not old or old["canonical_revision"] < p["canonical_revision"]:
            tx.put("listings", p["canonical_id"], p)
            active = tx.get("search_state", "active")
            if active:
                tx.put(active["projection_kind"], p["canonical_id"], p)
    elif typ.endswith(".command.execute"):
        from v2.shared.events import authorize

        authorize(p, admin=True)
        if p["action"] != "rebuild":
            raise ValueError("unsupported_search_action")
        events = tx.scan("event_archive")
        if not events:
            raise ValueError("snapshot_archive_unavailable")
        latest: dict[str, dict] = {}
        for item in events:
            snapshot = item["event"]["payload"]
            old = latest.get(snapshot["canonical_id"])
            if not old or snapshot["canonical_revision"] > old["canonical_revision"]:
                latest[snapshot["canonical_id"]] = snapshot
        kind = "rebuild:" + p["operation_id"]
        for key, row in latest.items():
            tx.put(kind, key, row)
        tx.put(
            "search_state",
            "active",
            {"id": "active", "projection_kind": kind, "count": len(latest)},
        )
        complete(tx, e, "search", {"projection_kind": kind, "count": len(latest)})
    elif typ == "control.flag.changed":
        tx.put("flags", p["flag"], p)


def query(tx, criteria):
    low = criteria.get("min_price")
    high = criteria.get("max_price")
    if low is not None and high is not None and low > high:
        raise ValueError("invalid_price_range")
    rows = []
    active = tx.get("search_state", "active")
    for row in tx.scan(active["projection_kind"] if active else "listings"):
        x = row["listing"]
        if (
            row["visibility"] != "active"
            or row["parent_canonical_id"]
            or x["price_period"] == "one_time"
        ):
            continue
        if not criteria.get("include_per_person", False) and x["price_basis"] == "per_person":
            continue
        if criteria.get("listing_type") and x.get("listing_type") != criteria["listing_type"]:
            continue
        price = x.get("price_normalized_monthly", x["price"])
        if (low is not None or high is not None) and price is None:
            continue
        if low is not None and price < low or high is not None and price > high:
            continue
        districts = criteria.get("districts") or (
            [criteria["district"]] if criteria.get("district") else []
        )
        if districts and (x["district"] or "").casefold() not in [s.casefold() for s in districts]:
            continue
        if criteria.get("rooms") is not None and x["rooms"] != criteria["rooms"]:
            continue
        if criteria.get("audience_tag") and criteria["audience_tag"] not in x["audience_tags"]:
            continue
        if criteria.get("renovation_level") and criteria["renovation_level"] != x.get(
            "renovation_level"
        ):
            continue
        rows.append(row)
    sort = criteria.get("sort", "newest")
    if sort in ("cheapest", "price_desc"):
        rows.sort(
            key=lambda r: (
                r["listing"].get("price_normalized_monthly", r["listing"]["price"]) is None,
                (r["listing"].get("price_normalized_monthly", r["listing"]["price"]) or 0)
                * (-1 if sort == "price_desc" else 1),
                r["canonical_id"],
            )
        )
    else:
        rows.sort(key=lambda r: (r.get("created_at", ""), r["canonical_id"]), reverse=True)
    offset = max(0, int(criteria.get("offset", 0)))
    limit = min(50, max(1, int(criteria.get("limit", 10))))
    return {
        "items": rows[offset : offset + limit],
        "metadata": {
            "total": len(rows),
            "returned": len(rows[offset : offset + limit]),
            "limit": limit,
            "offset": offset,
        },
        "mode": "FAKE_ONLY",
    }
