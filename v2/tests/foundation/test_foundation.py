"""Real PostgreSQL/RabbitMQ acceptance cases. No provider or transport mocks."""

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from v2.foundation import Broker, Store, dispatch_once, process_event, replay
from v2.shared.contracts import new_event


def event(version=1, aggregate=None):
    return new_event(
        "lab.synthetic.changed",
        "lab_producer",
        "synthetic",
        aggregate or str(uuid.uuid4()),
        version,
        {"value": 1},
    )


@pytest.fixture
def pair():
    producer, consumer = Store("lab_producer"), Store("lab_consumer")
    for store in (producer, consumer):
        with store.transaction() as tx:
            for table in (
                "records",
                "outbox",
                "inbox",
                "revisions",
                "retry_schedule",
                "parked_events",
                "replay_audit",
            ):
                tx.conn.execute(f"DELETE FROM {store.owner}.{table}")
    return producer, consumer


def effect(tx, value):
    old = tx.get("projection", value["aggregate_id"]) or {"count": 0}
    tx.put(
        "projection",
        value["aggregate_id"],
        {"count": old["count"] + 1, "version": value["aggregate_version"]},
    )


def count(store, table):
    with store.transaction() as tx:
        return tx.conn.execute(f"SELECT count(*) AS n FROM {store.owner}.{table}").fetchone()["n"]


def test_fnd01_rollback(pair):
    producer, _ = pair
    with pytest.raises(RuntimeError):
        with producer.transaction() as tx:
            tx.put("thing", "one", {"value": 1})
            tx.emit(event())
            raise RuntimeError("synthetic crash")
    assert count(producer, "records") == count(producer, "outbox") == 0


def test_fnd02_commit_before_dispatch(pair):
    producer, consumer = pair
    value = event()
    broker = Broker()
    queue = "v2.lab.consumer.events.v1"
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    with producer.transaction() as tx:
        tx.put("thing", "one", {"value": 1})
        tx.emit(value)
    assert count(producer, "outbox") == 1
    assert dispatch_once(producer, broker) == 1
    delivery = broker.get(queue)
    assert delivery is not None
    assert process_event(consumer, delivery.event, effect) == "applied"
    broker.ack(delivery)
    assert count(consumer, "records") == 1
    broker.close()


def test_fnd09_identical_duplicate(pair):
    _, consumer = pair
    value = event()
    assert process_event(consumer, value, effect) == "applied"
    assert process_event(consumer, copy.deepcopy(value), effect) == "duplicate"
    assert count(consumer, "inbox") == count(consumer, "records") == 1


def test_fnd10_changed_payload_conflict(pair):
    _, consumer = pair
    value = event()
    process_event(consumer, value, effect)
    value["payload"]["value"] = 2
    assert process_event(consumer, value, effect) == "payload_conflict"
    assert count(consumer, "parked_events") == 1


def test_fnd11_revision_conflict(pair):
    _, consumer = pair
    value = event()
    process_event(consumer, value, effect)
    conflict = event(aggregate=value["aggregate_id"])
    assert process_event(consumer, conflict, effect) == "revision_conflict"


def test_fnd12_gap_replay(pair):
    _, consumer = pair
    one = event()
    process_event(consumer, one, effect)
    three = event(3, one["aggregate_id"])
    assert process_event(consumer, three, effect) == "revision_gap"
    assert process_event(consumer, event(2, one["aggregate_id"]), effect) == "applied"
    parked = consumer.parked()[0]
    assert replay(consumer, parked["id"], "lab-operator", "redeliver") == three
    assert process_event(consumer, three, effect) == "applied"


def test_fnd13_known_stale(pair):
    _, consumer = pair
    one = event()
    process_event(consumer, one, effect)
    process_event(consumer, event(2, one["aggregate_id"]), effect)
    assert process_event(consumer, one, effect) == "duplicate"


def test_fnd15_commit_failure_rolls_back(pair):
    _, consumer = pair
    value = event()

    def fail(tx, value):
        effect(tx, value)
        tx.conn.execute("SELECT 1 / 0")

    with pytest.raises(psycopg.Error):
        process_event(consumer, value, fail)
    assert count(consumer, "records") == count(consumer, "inbox") == 0


def test_fnd16_17_retry_durable_backoff(pair):
    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def fail(tx, value):
        raise TimeoutError("token-shaped sensitive text")

    assert process_event(consumer, value, fail, now=now) == "retry_scheduled"
    assert process_event(consumer, value, effect, now=now) == "retry_wait"
    assert consumer.retries()[0]["attempts"] == 1
    assert consumer.retries()[0]["next_attempt_at"] > now
    assert count(consumer, "records") == 0


def test_fnd18_19_bounded_retry(pair):
    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def fail(tx, value):
        raise TimeoutError("transient")

    for attempt in range(6):
        status = process_event(consumer, value, fail, now=now + timedelta(seconds=1000 * attempt))
        assert status == ("terminal_failure" if attempt == 5 else "retry_scheduled")
    assert count(consumer, "parked_events") == 1
    assert (
        process_event(consumer, value, effect, now=now + timedelta(hours=2)) == "terminal_failure"
    )


@pytest.mark.parametrize("body", [b"{bad", b"x" * 65537, {"event_version": 999}])
def test_fnd20_poison_durable(pair, body):
    _, consumer = pair
    assert process_event(consumer, body, effect) in {
        "invalid_schema",
        "unsupported_version",
        "oversized",
    }
    assert count(consumer, "parked_events") == 1
    assert count(consumer, "records") == 0


def test_fnd21_22_replay_authorization_audit(pair):
    _, consumer = pair
    value = event(3)
    process_event(consumer, value, effect)
    parked = consumer.parked()[0]
    with pytest.raises(PermissionError):
        replay(consumer, parked["id"], "intruder", "redeliver")
    with pytest.raises(PermissionError):
        replay(consumer, parked["id"], "lab-operator", "force")
    assert count(consumer, "replay_audit") == 2
    assert (
        replay(consumer, parked["id"], "lab-operator", "redeliver")["event_id"] == value["event_id"]
    )


@pytest.mark.parametrize(
    "owner,other", [("lab_producer", "lab_consumer"), ("lab_consumer", "lab_producer")]
)
def test_fnd25_cross_schema_denied(owner, other):
    for statement in (
        f"SELECT * FROM {other}.records",
        f"INSERT INTO {other}.records VALUES ('x','x','{{}}')",
        f"SET ROLE ef_{other}_owner",
        "CREATE TABLE public.forbidden(x int)",
    ):
        with Store(owner).transaction() as tx:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                tx.conn.execute(statement)


def test_fnd29_safe_diagnostics(pair, caplog):
    _, consumer = pair
    value = event()

    def fail(tx, value):
        raise TimeoutError("private-session token password user@example.invalid")

    process_event(consumer, value, fail)
    assert "private-session" not in caplog.text
    assert consumer.retries()[0]["error_class"] == "TimeoutError"


def test_fnd30_concurrent_duplicates(pair):
    _, consumer = pair
    value = event()
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda _: process_event(consumer, value, effect), range(2)))
    assert sorted(statuses) == ["applied", "duplicate"]
    with consumer.transaction() as tx:
        assert tx.get("projection", value["aggregate_id"])["count"] == 1


def test_fnd31_gap_expiry(pair):
    _, consumer = pair
    value = event(3)
    assert process_event(consumer, value, effect) == "revision_gap"
    assert count(consumer, "records") == 0
    assert consumer.parked()[0]["reason"] == "revision_gap"
    from v2.foundation.messaging import expire_gaps

    assert expire_gaps(consumer, datetime.now(timezone.utc) + timedelta(minutes=11)) == 1
    assert consumer.parked()[0]["reason"] == "terminal_revision_gap"


def test_fnd34_no_v1_imports():
    from pathlib import Path

    for path in Path("/app/v2/foundation").rglob("*.py"):
        source = path.read_text()
        assert "from estateflow." not in source and "import estateflow." not in source
