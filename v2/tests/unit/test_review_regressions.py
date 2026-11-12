import copy
import json
from pathlib import Path
from v2.tests.unit.test_domains import Lab
from v2.shared.contracts import new_event, stable_id, validate


def extracted(generation, price):
    original = json.loads(
        Path("tests/fixtures/v2/contracts/08-precheck-extract-review.json").read_text()
    )["steps"][1]["event"]
    p = copy.deepcopy(original["payload"])
    cid = stable_id("order-candidate", generation)
    batch = stable_id("order-batch", generation)
    p["lineage"].update(candidate_id=cid, batch_id=batch, generation=generation)
    p["lineage"]["source_refs"] = [
        {"message_uid": stable_id("order-source"), "source_revision": generation}
    ]
    p["listing"]["price"] = price
    p["outcome"] = "accepted"
    event = new_event("candidate.extracted", "ai", "candidate", cid, 1, p)
    manifest = new_event(
        "parser.batch.completed",
        "source_parser",
        "parser_batch",
        batch,
        generation,
        {
            "batch_id": batch,
            "generation": generation,
            "source_refs": p["lineage"]["source_refs"],
            "policy": p["lineage"]["policy"],
            "candidate_ids": [cid],
            "supersedes_batch_id": stable_id("order-batch", generation - 1)
            if generation > 1
            else None,
            "retracts_candidate_ids": [stable_id("order-candidate", generation - 1)]
            if generation > 1
            else [],
            "outcome": "complete",
        },
    )
    return event, manifest


def test_catalog_delayed_older_generation_never_overwrites_newer_evidence():
    lab = Lab()
    for generation in (1, 3, 2):
        for event in extracted(generation, generation * 100):
            lab.handle("catalog", event)
        lab.pump()
    active = [x for x in lab.rows("catalog", "listings") if x["visibility"] == "active"]
    assert len(active) == 1 and active[0]["listing"]["price"] == 300


def test_approved_digest_replacement_retracts_old_generation_atomically():
    lab = Lab()
    lab.command("listener", "set_subscription", "review-digest", recipe_key="generic.digest_blocks")
    lab.pump()
    lab.command(
        "listener",
        "ingest",
        "digest",
        source_id="review-digest",
        channel_id="c",
        message_id="digest",
        text="1. SYNTHETIC rent USD 100\n2. SYNTHETIC rent USD 200",
    )
    lab.pump()
    uid = lab.rows("catalog", "listings")[0]["source_evidence"][0]["message_uid"]
    lab.command(
        "source_parser",
        "reparse",
        uid,
        recipe_key="generic.single_listing",
        reason="synthetic review",
    )
    lab.pump()
    pending = next(r for r in lab.rows("catalog", "listings") if r["visibility"] == "review")
    lab.command(
        "catalog",
        "review",
        pending["canonical_id"],
        approved=True,
        reason="synthetic replacement approved",
    )
    lab.pump()
    assert len([x for x in lab.rows("catalog", "listings") if x["visibility"] == "active"]) == 1
    assert len([x for x in lab.rows("catalog", "listings") if x["visibility"] == "hidden"]) == 2


def test_action_specific_missing_merge_target_rejected_by_schema():
    operation = stable_id("invalid-merge")
    e = new_event(
        "catalog.command.execute",
        "catalog",
        "command",
        operation,
        1,
        {
            "operation_id": operation,
            "action": "merge",
            "subject_id": "synthetic",
            "actor": {"id": "admin", "roles": ["admin"]},
            "expected_version": None,
            "arguments": {},
        },
    )
    try:
        validate(e)
    except ValueError:
        return
    raise AssertionError("merge without target_id passed contract")


def test_terminal_content_failure_is_not_automatically_republished():
    lab = Lab()
    lab.command("content", "schedule", "terminal")
    lab.pump()
    lab.command("content", "publish", "terminal", sender_mode="permanent")
    lab.pump()
    lab.command("content", "publish", "terminal", sender_mode="success")
    lab.pump()
    assert lab.rows("content", "posts")[0]["status"] == "failed"
    assert len(lab.rows("content", "attempts")) == 1


def test_search_rebuild_uses_event_archive_in_new_projection_namespace():
    from v2.services.search.handler import query

    lab = Lab()
    for event in extracted(1, 100):
        lab.handle("catalog", event)
    lab.pump()
    tx = lab.stores["search"]
    row = lab.rows("search", "listings")[0]
    row["listing"]["price"] = 999
    tx.put("listings", row["canonical_id"], row)
    lab.command("search", "rebuild", "synthetic-projection")
    lab.pump()
    assert query(tx, {})["items"][0]["listing"]["price"] == 100


def test_empty_complete_generation_preserves_old_until_explicit_batch_review():
    lab = Lab()
    for e in extracted(1, 100):
        lab.handle("catalog", e)
    lab.pump()
    _, manifest = extracted(2, 200)
    manifest["payload"]["candidate_ids"] = []
    lab.handle("catalog", manifest)
    lab.pump()
    assert len([x for x in lab.rows("catalog", "listings") if x["visibility"] == "active"]) == 1
    pending = lab.rows("catalog", "pending_batches")[0]
    assert pending["status"] == "pending" and pending["new_ids"] == []
    lab.command(
        "catalog",
        "review_batch",
        pending["id"],
        approved=True,
        reason="synthetic empty reparse accepted",
    )
    lab.pump()
    assert all(x["visibility"] == "hidden" for x in lab.rows("catalog", "listings"))


def test_failed_source_approval_saga_is_explicit_and_does_not_activate():
    lab = Lab()
    lab.command(
        "control",
        "suggest_source",
        "failed-saga",
        source_id="synthetic-failed",
        user_id="synthetic-user",
    )
    lab.pump()
    lab.command("control", "approve_source", "failed-saga")
    lab.events = []
    saga = lab.rows("control", "sagas")[0]
    fact = new_event(
        "operation.completed",
        "identity",
        "domain_record",
        saga["operations"][-1],
        1,
        {
            "operation_id": saga["operations"][-1],
            "owner": "identity",
            "subject_id": "synthetic-user",
            "status": "failed",
            "reason": "permanent_failure",
        },
    )
    lab.handle("control", fact)
    lab.pump()
    assert lab.rows("control", "sagas")[0]["status"] == "failed"
    assert lab.rows("control", "suggestions")[0]["status"] == "approval_failed"
    assert not any(e["payload"].get("arguments", {}).get("enabled") is True for e in lab.events)


def test_media_retry_decodes_capture_without_repeating_extraction():
    lab = Lab()
    lab.command(
        "listener",
        "ingest",
        "retry-media",
        source_id="synthetic-media-retry",
        channel_id="c",
        message_id="retry-media",
        text="SYNTHETIC rent USD 100",
        media_capture_refs=["offline://fixtures/expired"],
    )
    lab.pump()
    before = lab.rows("ai", "runs")
    cid = before[0]["id"]
    lab.command(
        "ai",
        "media_retry",
        cid,
        reason="synthetic recovered capture",
        media_capture_refs=["offline://fixtures/image.png"],
    )
    lab.pump()
    assert lab.rows("ai", "runs") == before
    assert lab.rows("catalog", "listings")[0]["media"]["state"] == "ready"
    assert lab.rows("ai", "media_jobs")[0]["revision"] == 2
    assert len(lab.rows("ai", "media_audit")) == 1


def test_stale_entitlement_parks_and_refresh_resumes_with_current_priority():
    from v2.tests.unit.test_domains import DomainTests

    lab = DomainTests().setup_listing()
    tx = lab.stores["notifications"]
    user = tx.get("users", "synthetic-user")
    user["expires_at"] = 0
    tx.put("users", "synthetic-user", user)
    lab.command("notifications", "deliver", sender_mode="success")
    lab.pump()
    assert lab.rows("notifications", "jobs")[0]["status"] == "parked_entitlement"
    assert not lab.rows("notifications", "attempts")
    lab.command(
        "identity",
        "grant_premium",
        "synthetic-user",
        user_id="synthetic-user",
        reason="synthetic entitlement refreshed",
    )
    lab.pump()
    lab.command("notifications", "deliver", "fresh", sender_mode="success")
    lab.pump()
    assert lab.rows("notifications", "jobs")[0]["status"] == "sent"
    assert lab.rows("notifications", "attempts")[0]["premium_at_send"] is True


def test_analytics_record_changes_counter():
    lab = Lab()
    lab.command("analytics", "record", "synthetic-metric", kind="synthetic.operator.recorded")
    lab.pump()
    assert (
        next(
            r for r in lab.rows("analytics", "metrics") if r["id"] == "synthetic.operator.recorded"
        )["count"]
        == 1
    )


def test_media_revision_cannot_regress_after_a_newer_retry_result():
    lab = Lab()
    extraction, manifest = extracted(1, 100)
    lab.handle("catalog", extraction)
    lab.handle("catalog", manifest)
    lab.pump()
    cid = extraction["payload"]["lineage"]["candidate_id"]
    job = stable_id("media-job", cid)
    for revision, state in ((2, "unavailable"), (1, "failed")):
        event = new_event(
            "media." + state,
            "ai",
            "media_job",
            job,
            revision,
            {
                "media_job_id": job,
                "candidate_id": cid,
                "source_revision": 1,
                "media_revision": revision,
                "media": {
                    "state": state,
                    "assets": [],
                    "failure_code": "capture_unavailable" if state == "failed" else None,
                },
            },
        )
        lab.handle("catalog", event)
        lab.pump()
    assert lab.rows("catalog", "listings")[0]["media"]["state"] == "unavailable"


def test_corrected_generation_rebinds_pending_review_to_current_content():
    lab = Lab()
    first, manifest = extracted(1, 100)
    first["payload"]["outcome"] = "review_required"
    lab.handle("catalog", first)
    lab.handle("catalog", manifest)
    lab.pump()
    for event in extracted(2, 200):
        lab.handle("catalog", event)
    lab.pump()
    current = next(x for x in lab.rows("catalog", "listings") if x["listing"]["price"] == 200)
    assert current["visibility"] == "review"
    lab.command(
        "catalog",
        "review",
        current["canonical_id"],
        approved=True,
        reason="synthetic corrected interpretation",
    )
    lab.pump()
    active = [x for x in lab.rows("catalog", "listings") if x["visibility"] == "active"]
    assert len(active) == 1 and active[0]["listing"]["price"] == 200
