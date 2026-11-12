from contextlib import contextmanager
from v2.tests.unit.test_domains import Tx
from v2.shared.contracts import new_event, stable_id
from v2.runner import record_outcome


class Store:
    owner = "catalog"

    def __init__(self):
        self.tx = Tx()
        self.tx.owner = self.owner

    @contextmanager
    def transaction(self):
        yield self.tx


def test_permanent_command_failure_finishes_operation_without_retrying_forever():
    store = Store()
    operation = stable_id("failed-operation")
    store.tx.put(
        "operations",
        operation,
        {"id": operation, "status": "accepted", "actor_id": "synthetic-admin"},
    )
    event = new_event(
        "catalog.command.execute",
        "catalog",
        "command",
        operation,
        1,
        {
            "operation_id": operation,
            "action": "review",
            "subject_id": "not-found",
            "actor": {"id": "synthetic-admin", "roles": ["admin"]},
            "expected_version": None,
            "arguments": {"approved": True},
        },
    )
    record_outcome(store, event, "permanent_failure")
    assert store.tx.get("operations", operation)["status"] == "failed"
    assert store.tx.out[-1]["payload"]["status"] == "failed"


def test_failed_delegated_command_without_http_operation_still_emits_failure():
    store = Store()
    operation = stable_id("delegated-failed-operation")
    event = new_event(
        "catalog.command.execute",
        "control",
        "command",
        operation,
        1,
        {
            "operation_id": operation,
            "action": "review",
            "subject_id": "missing",
            "actor": {"id": "lab-orchestrator", "roles": ["service"]},
            "expected_version": None,
            "arguments": {"approved": True},
        },
    )
    record_outcome(store, event, "permanent_failure")
    assert store.tx.get("operations", operation)["status"] == "failed"
    assert store.tx.out[-1]["payload"]["operation_id"] == operation
    record_outcome(store, event, "permanent_failure")
    assert len(store.tx.out) == 1
