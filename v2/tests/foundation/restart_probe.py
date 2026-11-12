"""Invoked by host lab fault driver around genuine scoped Docker restarts."""

import sys
import uuid
from datetime import datetime, timedelta, timezone

from v2.foundation import Broker, Store, dispatch_once, process_event
from v2.shared.contracts import new_event
from .test_foundation import effect

action = sys.argv[1]
producer, consumer = Store("lab_producer"), Store("lab_consumer")
queue = "v2.lab.restart.events.v1"
if action == "prepare":
    broker = Broker()
    broker.declare(queue, ["lab.synthetic.changed.v1"])
    broker.purge(queue)
    value = new_event(
        "lab.synthetic.changed", "lab_producer", "synthetic", str(uuid.uuid4()), 1, {"value": 1}
    )
    with producer.transaction() as tx:
        tx.put("restart_probe", "event", value)
        tx.emit(value)
    assert dispatch_once(producer, broker) >= 1
    broker.close()
    print("FND-23 PREPARED confirmed persistent broker message + committed outbox")
elif action == "after_broker":
    broker = Broker()
    delivery = broker.get(queue)
    assert delivery is not None
    assert process_event(consumer, delivery.event, effect) == "applied"
    broker.ack(delivery)
    broker.close()
    print("FND-23 PASS confirmed message survived genuine RabbitMQ container restart")
elif action == "after_db":
    with producer.transaction() as tx:
        value = tx.get("restart_probe", "event")
        assert value
        outbox = tx.conn.execute(
            "SELECT status FROM lab_producer.outbox WHERE event_id=%s", (value["event_id"],)
        ).fetchone()
        assert outbox["status"] == "published"
    with consumer.transaction() as tx:
        assert tx.get("projection", value["aggregate_id"])["count"] == 1
        assert tx.conn.execute(
            "SELECT 1 FROM lab_consumer.inbox WHERE event_id=%s", (value["event_id"],)
        ).fetchone()
    print("FND-24 PASS committed outbox/inbox/effect survived genuine PostgreSQL container restart")
elif action == "outage":
    value = new_event(
        "lab.synthetic.changed", "lab_producer", "synthetic", str(uuid.uuid4()), 1, {"value": 1}
    )
    with producer.transaction() as tx:
        tx.put("outage_probe", "event", value)
        tx.emit(value)

    class OfflineBroker:
        def publish(self, value):
            broker = Broker()
            broker.publish(value)

    assert dispatch_once(producer, OfflineBroker()) == 0
    with producer.transaction() as tx:
        row = tx.conn.execute(
            "SELECT * FROM lab_producer.outbox WHERE event_id=%s", (value["event_id"],)
        ).fetchone()
        assert row["status"] == "pending" and row["attempts"] == 1 and row["lease_token"] is None
    print("FND-05 PREPARED real broker stopped; durable retry intent survives failed connection")
elif action == "after_outage":
    broker = Broker()
    assert (
        dispatch_once(producer, broker, now=datetime.now(timezone.utc) + timedelta(seconds=7)) >= 1
    )
    broker.close()
    print("FND-05 PASS broker restart recovers accepted pending outbox")
else:
    raise ValueError("Unknown fault probe")
