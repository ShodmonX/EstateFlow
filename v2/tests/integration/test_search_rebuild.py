"""Real PostgreSQL serialization between projection rebuilding and live snapshots."""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from uuid import uuid4
import pytest
from v2.foundation import Store
from v2.services.search.handler import handle, query
from v2.shared.contracts import new_event, stable_id
from v2.tests.unit.test_review_regressions import extracted


@pytest.mark.integration
def test_rebuild_and_live_snapshot_share_owner_mutation_lock():
    store = Store("search")
    token = uuid4().hex
    canonical = stable_id(token)
    extraction, _ = extracted(1, 100)
    p = extraction["payload"]
    snapshot = {
        "canonical_id": canonical,
        "canonical_revision": 1,
        "mutation_id": stable_id(token, 1),
        "reason": "created",
        "visibility": "active",
        "parent_canonical_id": None,
        "source_evidence": [
            {
                "message_uid": stable_id(token, "message"),
                "source_revision": 1,
                "state": "active",
                "candidate_id": p["lineage"]["candidate_id"],
            }
        ],
        "listing": p["listing"],
        "media": p["media"],
        "review": "none",
        "redirect_from_ids": [],
        "related_canonical_ids": [],
    }
    first = new_event(
        "catalog.listing.changed", "catalog", "canonical_listing", canonical, 1, snapshot
    )
    with store.transaction() as tx:
        handle(tx, first)
    scanned = threading.Event()
    release = threading.Event()

    class Paused:
        def __init__(self, tx):
            self.tx = tx

        def __getattr__(self, key):
            return getattr(self.tx, key)

        def scan(self, kind):
            rows = self.tx.scan(kind)
            if kind == "event_archive":
                scanned.set()
                assert release.wait(10)
            return rows

    op = stable_id(token, "rebuild")
    command = new_event(
        "search.command.execute",
        "search",
        "command",
        op,
        1,
        {
            "operation_id": op,
            "action": "rebuild",
            "subject_id": "synthetic-concurrent",
            "actor": {"id": "admin", "roles": ["admin"]},
            "expected_version": None,
            "arguments": {},
        },
    )

    def rebuilding():
        with store.transaction() as tx:
            handle(Paused(tx), command)

    def updating():
        updated = {
            **snapshot,
            "canonical_revision": 2,
            "mutation_id": stable_id(token, 2),
            "reason": "edited",
            "listing": {**p["listing"], "price": 222},
        }
        event = new_event(
            "catalog.listing.changed", "catalog", "canonical_listing", canonical, 2, updated
        )
        with store.transaction() as tx:
            handle(tx, event)

    with ThreadPoolExecutor(2) as pool:
        rebuild = pool.submit(rebuilding)
        assert scanned.wait(10)
        live = pool.submit(updating)
        try:
            with pytest.raises(FutureTimeout):
                live.result(timeout=0.3)
        finally:
            release.set()
        rebuild.result(timeout=10)
        live.result(timeout=10)
    with store.transaction() as tx:
        found = next(
            x for x in query(tx, {"limit": 100})["items"] if x["canonical_id"] == canonical
        )
        assert found["canonical_revision"] == 2 and found["listing"]["price"] == 222
