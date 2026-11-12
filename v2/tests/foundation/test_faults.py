"""Fault boundaries use real PostgreSQL/RabbitMQ plus disposable child processes."""

import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from v2.foundation import Broker, Store, dispatch_once, process_event, retry_due
from v2.foundation.db import OWNERS, migrate
from v2.foundation.messaging import DELAYS, claim, mark_published
from .test_foundation import count, effect, event


def child(code, value=None):
    return subprocess.run(
        [sys.executable, "-c", code], input=json.dumps(value), text=True, capture_output=True
    )


def test_fnd03_producer_process_killed_after_commit(pair):
    producer, _ = pair
    value = event()
    result = child(
        """import json,os,sys
from v2.foundation import Store
event=json.load(sys.stdin)
with Store('lab_producer').transaction() as tx:
 tx.put('thing',event['aggregate_id'],{'value':1})
 tx.emit(event)
os._exit(91)
""",
        value,
    )
    assert result.returncode == 91
    assert count(producer, "records") == count(producer, "outbox") == 1


def test_fnd04_confirm_before_mark_process_crash(pair):
    producer, consumer = pair
    value = event()
    broker = Broker()
    queue = "v2.lab.fnd04.events.v1"
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    with producer.transaction() as tx:
        tx.emit(value)
    result = child("""import os
from v2.foundation import Store,Broker,dispatch_once
class CrashBroker(Broker):
 def publish(self,event,routing_key=None):
  super().publish(event,routing_key)
  os._exit(92)
dispatch_once(Store('lab_producer'),CrashBroker())
""")
    assert result.returncode == 92
    first = broker.get(queue)
    assert first.event["event_id"] == value["event_id"]
    assert process_event(consumer, first.event, effect) == "applied"
    broker.ack(first)
    later = datetime.now(timezone.utc) + timedelta(seconds=61)
    assert dispatch_once(producer, broker, now=later) == 1
    duplicate = broker.get(queue)
    assert process_event(consumer, duplicate.event, effect) == "duplicate"
    broker.ack(duplicate)
    broker.close()


def test_fnd06_ambiguous_confirm_keeps_identity(pair):
    producer, consumer = pair
    value = event()
    broker = Broker()
    queue = "v2.lab.fnd06.events.v1"
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    with producer.transaction() as tx:
        tx.emit(value)

    class AmbiguousBroker:
        def publish(self, value):
            broker.publish(value)
            raise TimeoutError("injected after real broker confirmation")

    assert dispatch_once(producer, AmbiguousBroker()) == 0
    assert (
        dispatch_once(producer, broker, now=datetime.now(timezone.utc) + timedelta(seconds=6)) == 1
    )
    events = [broker.get(queue), broker.get(queue)]
    assert all(item.event["event_id"] == value["event_id"] for item in events)
    assert [process_event(consumer, item.event, effect) for item in events] == [
        "applied",
        "duplicate",
    ]
    for item in events:
        broker.ack(item)
    broker.close()


def test_fnd07_concurrent_dispatch_claim(pair):
    producer, _ = pair
    with producer.transaction() as tx:
        tx.emit(event())
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(lambda _: claim(producer, "outbox", 1, now), range(2)))
    assert sum(map(len, rows)) == 1


def test_fnd08_lease_fencing(pair):
    producer, _ = pair
    with producer.transaction() as tx:
        tx.emit(event())
    now = datetime.now(timezone.utc)
    old = claim(producer, "outbox", 1, now)[0]
    new = claim(producer, "outbox", 1, now + timedelta(seconds=61))[0]
    assert old["lease_token"] != new["lease_token"]
    assert not mark_published(
        producer, old["event_id"], old["lease_token"], now + timedelta(seconds=61)
    )
    assert mark_published(
        producer, new["event_id"], new["lease_token"], now + timedelta(seconds=62)
    )


def test_fnd14_33_consumer_crash_before_ack(pair):
    _, consumer = pair
    value = event()
    broker = Broker()
    queue = "v2.lab.fnd14.events.v1"
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    broker.publish(value)
    result = child("""import os
from v2.foundation import Store,Broker,process_event
from v2.tests.foundation.test_foundation import effect
broker=Broker()
delivery=broker.get('v2.lab.fnd14.events.v1')
assert process_event(Store('lab_consumer'),delivery.event,effect)=='applied'
os._exit(93)
""")
    assert result.returncode == 93
    import time

    deadline = time.monotonic() + 10
    duplicate = None
    while duplicate is None and time.monotonic() < deadline:
        duplicate = broker.get(queue)
        if duplicate is None:
            time.sleep(0.05)
    assert duplicate is not None
    assert process_event(consumer, duplicate.event, effect) == "duplicate"
    broker.ack(duplicate)
    broker.close()
    assert count(consumer, "inbox") == count(consumer, "records") == 1


def test_fnd18_retry_delay_budget():
    assert DELAYS == (5, 15, 45, 135, 405)
    from v2.foundation.messaging import retry_delay

    for attempt, base in enumerate(DELAYS, 1):
        delay = retry_delay("fixed-synthetic-event", attempt)
        assert base * 0.8 <= delay <= base * 1.2
        assert delay == retry_delay("fixed-synthetic-event", attempt)


def test_fnd19_expired_park(pair):
    _, consumer = pair
    value = event()
    assert (
        process_event(consumer, value, effect, now=datetime.now(timezone.utc) + timedelta(hours=25))
        == "expired"
    )
    assert count(consumer, "parked_events") == 1


@pytest.mark.parametrize("owner", OWNERS)
def test_fnd26_owner_future_defaults(owner):
    name = "probe_" + uuid.uuid4().hex[:10]
    with psycopg.connect(os.environ["EFV2_ADMIN_DSN"], autocommit=True) as admin:
        admin.execute(f"SET ROLE ef_{owner}_owner")
        admin.execute(f"CREATE TABLE {owner}.{name}(id bigserial PRIMARY KEY, value integer)")
        with Store(owner).transaction() as tx:
            assert (
                tx.conn.execute(
                    f"INSERT INTO {owner}.{name}(value) VALUES(9) RETURNING id"
                ).fetchone()["id"]
                == 1
            )
        other = "lab_consumer" if owner != "lab_consumer" else "lab_producer"
        with Store(other).transaction() as tx:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                tx.conn.execute(f"SELECT nextval('{owner}.{name}_id_seq')")
        admin.execute(f"DROP TABLE {owner}.{name}")


def test_fnd27_exchange_coexistence():
    broker = Broker()
    broker.channel.exchange_declare("estateflow.events", exchange_type="direct", durable=True)
    broker.channel.exchange_declare("estateflow.v2.events", exchange_type="topic", durable=True)
    broker.close()


def test_fnd28_private_compose():
    source = Path("/app/infra/v2/compose.yaml").read_text()
    assert "ports:" not in source
    assert "network_mode:" not in source
    assert "internal: true" in source
    assert "docker.sock" not in source
    assert "external:" not in source


def test_fnd32_repeat_bootstrap():
    migrate()
    migrate()
    for owner in OWNERS:
        with Store(owner).transaction() as tx:
            assert [
                r["version"]
                for r in tx.conn.execute(
                    f"SELECT version FROM {owner}.schema_versions ORDER BY version"
                ).fetchall()
            ] == [1, 2]


def test_retry_due_real_broker(pair):
    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def transient(tx, value):
        raise TimeoutError("injected")

    assert process_event(consumer, value, transient, now=now) == "retry_scheduled"
    broker = Broker()
    queue = "v2.lab.retry.events.v1"
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    assert retry_due(consumer, broker, now=now) == 0
    assert retry_due(consumer, broker, now=now + timedelta(seconds=6)) == 1
    delivery = broker.get(queue)
    assert (
        process_event(consumer, delivery.event, effect, now=now + timedelta(seconds=6)) == "applied"
    )
    broker.ack(delivery)
    broker.close()


def test_fnd17_retry_process_crash_before_ack(pair):
    _, consumer = pair
    value = event()
    result = child(
        """import json,os,sys
from v2.foundation import Store,process_event
def fail(tx,event): raise TimeoutError('synthetic')
assert process_event(Store('lab_consumer'),json.load(sys.stdin),fail)=='retry_scheduled'
os._exit(94)
""",
        value,
    )
    assert result.returncode == 94
    assert process_event(consumer, value, effect) == "retry_wait"
    assert len(consumer.retries()) == 1
    assert count(consumer, "records") == 0


def test_fnd15_actual_deferred_commit_failure(pair):
    _, consumer = pair
    name = "commit_probe_" + uuid.uuid4().hex[:10]
    with psycopg.connect(os.environ["EFV2_ADMIN_DSN"], autocommit=True) as admin:
        admin.execute("SET ROLE ef_lab_consumer_owner")
        admin.execute(f"CREATE TABLE lab_consumer.{name}_parent(id integer PRIMARY KEY)")
        admin.execute(
            f"CREATE TABLE lab_consumer.{name}_child(id integer REFERENCES lab_consumer.{name}_parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )

        def deferred_failure(tx, value):
            effect(tx, value)
            tx.conn.execute(f"INSERT INTO lab_consumer.{name}_child VALUES(1)")

        try:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                process_event(consumer, event(), deferred_failure)
            assert count(consumer, "records") == count(consumer, "inbox") == 0
        finally:
            admin.execute(f"DROP TABLE lab_consumer.{name}_child")
            admin.execute(f"DROP TABLE lab_consumer.{name}_parent")


def test_guards_before_connect():
    with pytest.raises(PermissionError):
        Store("catalog", "postgresql://fake:fake@external.example/production")
    with pytest.raises(PermissionError):
        Broker("amqp://fake:fake@external.example/")


def test_routing_and_permanent_failures_park(pair):
    _, consumer = pair
    assert (
        process_event(consumer, event(), effect, routing_key="wrong.route.v1") == "routing_conflict"
    )

    def permanent(tx, value):
        raise ValueError("private synthetic payload")

    assert process_event(consumer, event(), permanent) == "permanent_failure"
    assert count(consumer, "records") == count(consumer, "inbox") == 0


def test_terminal_retry_authorized_replay_can_recover(pair):
    from v2.foundation import replay

    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def fail(tx, value):
        raise TimeoutError("synthetic transient")

    for attempt in range(6):
        process_event(consumer, value, fail, now=now + timedelta(seconds=1000 * attempt))
    parked = consumer.parked()[0]
    recovered = replay(consumer, parked["id"], "lab-operator")
    assert recovered == value
    assert process_event(consumer, recovered, effect, now=now + timedelta(hours=2)) == "applied"
    assert len(consumer.retries()) == 0
    assert count(consumer, "replay_audit") == 1
    assert count(consumer, "inbox") == 1


def test_retry_transport_failures_have_separate_finite_budget(pair):
    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def transient(tx, value):
        raise TimeoutError("synthetic handler transient")

    process_event(consumer, value, transient, now=now)

    class Unavailable:
        def publish(self, value):
            raise ConnectionError("synthetic unavailable transport")

    for attempt in range(6):
        retry_due(consumer, Unavailable(), now=now + timedelta(seconds=(attempt + 1) * 1000))
    row = consumer.retries()[0]
    assert row["status"] == "terminal"
    assert row["attempts"] == 1
    assert row["transport_attempts"] == 6


def test_unexpected_handler_error_uses_bounded_durable_processing_budget(pair):
    _, consumer = pair
    value = event()
    now = datetime.now(timezone.utc)

    def broken(tx, value):
        raise KeyError("synthetic malformed internal state")

    for attempt in range(6):
        status = process_event(consumer, value, broken, now=now + timedelta(seconds=attempt * 1000))
        assert status == ("terminal_failure" if attempt == 5 else "retry_scheduled")
    assert consumer.retries()[0]["attempts"] == 6
    assert count(consumer, "inbox") == count(consumer, "records") == 0
