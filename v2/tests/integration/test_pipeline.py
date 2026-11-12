"""Real owner-role PostgreSQL and RabbitMQ; all provider effects are explicitly fake."""

import importlib
import os
import time
from uuid import uuid4
import pytest
from v2.foundation import Store, Broker, process_event, dispatch_once, retry_due
from v2.shared.contracts import new_event, stable_id
from v2.shared.routing import OWNERS, BINDINGS

pytestmark = pytest.mark.integration


class Bus:
    def __init__(self):
        self.stores = {o: Store(o) for o in OWNERS}
        self.broker = Broker()
        self.prefix = "v2.pipeline." + uuid4().hex
        self.queues = {o: self.prefix + "." + o for o in OWNERS}
        for o, q in self.queues.items():
            self.broker.declare(q, BINDINGS[o])

    def command(self, owner, action, subject, **args):
        operation = str(uuid4())
        event = new_event(
            owner + ".command.execute",
            owner,
            "command",
            operation,
            1,
            {
                "operation_id": operation,
                "action": action,
                "subject_id": subject,
                "actor": {"id": "synthetic-integration-admin", "roles": ["admin"]},
                "expected_version": None,
                "arguments": args,
            },
        )
        with self.stores[owner].transaction() as tx:
            tx.emit(event)
        return operation

    def pump(self, timeout=25):
        deadline = time.monotonic() + timeout
        idle = 0
        while time.monotonic() < deadline:
            work = 0
            for owner, store in self.stores.items():
                work += dispatch_once(store, self.broker)
                work += retry_due(store, self.broker)
                for _ in range(20):
                    message = self.broker.get(self.queues[owner])
                    if not message:
                        break
                    assert message.routing_key == message.event["event_type"] + ".v" + str(
                        message.event["event_version"]
                    )
                    status = process_event(
                        store,
                        message.event,
                        importlib.import_module("v2.services." + owner + ".handler").handle,
                    )
                    assert status in ("applied", "duplicate"), (owner, status)
                    self.broker.ack(message)
                    work += 1
            if not work:
                idle += 1
                if idle >= 2:
                    return
            else:
                idle = 0
        raise AssertionError("pipeline did not quiesce before deadline")

    def rows(self, owner, kind):
        return self.stores[owner].scan(kind)

    def close(self):
        for q in self.queues.values():
            self.broker.channel.queue_delete(q)
            self.broker.channel.queue_delete(q + ".dead")
        self.broker.close()


@pytest.fixture
def bus():
    if "EFV2_DSN_LISTENER" not in os.environ:
        pytest.skip("private Compose test environment required")
    result = Bus()
    try:
        yield result
    finally:
        result.close()


def test_real_pipeline_transactional_changes_and_fake_send(bus):
    suffix = uuid4().hex
    user = "synthetic-user-" + suffix
    source = "synthetic-source-" + suffix
    fid = "filter-" + suffix
    bus.command("identity", "upsert_user", user, user_id=user, consent=True)
    bus.pump()
    bus.command(
        "notifications",
        "save_filter",
        fid,
        user_id=user,
        filter_id=fid,
        criteria={"max_price": 200},
    )
    bus.pump()
    bus.command(
        "listener",
        "ingest",
        "m1",
        source_id=source,
        channel_id="synthetic-channel",
        message_id="m1",
        text="SYNTHETIC rent 100 USD 2 rooms",
        update_type="received",
    )
    bus.pump()
    uid = stable_id("message", source, "synthetic-channel", "m1")
    listings = [
        r
        for r in bus.rows("search", "listings")
        if any(x["message_uid"] == uid for x in r["source_evidence"])
    ]
    assert len(listings) == 1 and listings[0]["visibility"] == "active"
    canonical = listings[0]["canonical_id"]
    jobs = [r for r in bus.rows("notifications", "jobs") if r["user_id"] == user]
    assert len(jobs) == 1 and jobs[0]["status"] == "pending"
    bus.command("notifications", "deliver", "sender-" + suffix, sender_mode="success")
    bus.pump()
    jobs = [r for r in bus.rows("notifications", "jobs") if r["user_id"] == user]
    assert jobs[0]["status"] == "sent"
    for price in (120, 130):
        bus.command(
            "listener",
            "ingest",
            "m1",
            source_id=source,
            channel_id="synthetic-channel",
            message_id="m1",
            text=f"SYNTHETIC rent {price} USD 2 rooms",
            update_type="edited",
        )
        bus.pump()
    current = next(r for r in bus.rows("search", "listings") if r["canonical_id"] == canonical)
    assert current["listing"]["price"] == 130
    bus.command(
        "listener",
        "ingest",
        "m1",
        source_id=source,
        channel_id="synthetic-channel",
        message_id="m1",
        update_type="deleted",
    )
    bus.pump()
    current = next(r for r in bus.rows("search", "listings") if r["canonical_id"] == canonical)
    assert current["visibility"] == "hidden"
    assert len([r for r in bus.rows("notifications", "jobs") if r["user_id"] == user]) == 1
    assert bus.stores["catalog"].metrics()["inbox"] > 0


def test_real_source_approval_saga_and_taxonomy_events(bus):
    suffix = uuid4().hex
    user = "synthetic-referral-" + suffix
    suggestion = "suggestion-" + suffix
    bus.command("identity", "upsert_user", user, user_id=user)
    bus.pump()
    bus.command(
        "control",
        "suggest_source",
        suggestion,
        user_id=user,
        source_id="synthetic-new-" + suffix,
    )
    bus.pump()
    bus.command("control", "approve_source", suggestion, recipe_key="generic.digest_blocks")
    bus.pump()
    assert any(
        r["id"] == "synthetic-new-" + suffix and r["enabled"]
        for r in bus.rows("listener", "subscriptions")
    )
    assert next(r for r in bus.rows("identity", "users") if r["user_id"] == user)["premium"]
    bus.command(
        "taxonomy",
        "approve_tag",
        "synthetic-tag-" + suffix,
        tag="synthetic-tag-" + suffix,
    )
    bus.pump()
    assert any(r["tag"] == "synthetic-tag-" + suffix for r in bus.rows("taxonomy", "tags"))
