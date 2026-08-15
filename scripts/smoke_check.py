from __future__ import annotations

from fastapi.testclient import TestClient

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.services.health import DependencyStatus
from estateflow.services.ops_notifications import DisabledOpsNotificationService


class StubHealthChecker:
    def __init__(self, statuses: list[DependencyStatus]) -> None:
        self._statuses = statuses

    async def check(self) -> list[DependencyStatus]:
        return self._statuses


def main() -> None:
    settings = Settings(environment="test")

    ok_app = create_app(
        settings,
        health_checker=StubHealthChecker(
            [
                DependencyStatus(name="postgres", status="ok"),
                DependencyStatus(name="redis", status="ok"),
            ]
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )
    ok_client = TestClient(ok_app)
    live_response = ok_client.get("/health/live", headers={"x-correlation-id": "smoke-local"})
    ready_response = ok_client.get("/health/ready")
    assert live_response.status_code == 200
    assert live_response.headers["x-correlation-id"] == "smoke-local"
    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ok"

    failed_app = create_app(
        settings,
        health_checker=StubHealthChecker(
            [
                DependencyStatus(name="postgres", status="error", error="TimeoutError"),
                DependencyStatus(name="redis", status="ok"),
            ]
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )
    failed_response = TestClient(failed_app).get("/health/ready")
    assert failed_response.status_code == 503
    assert failed_response.json()["status"] == "error"

    print("sprint0_smoke_ok")


if __name__ == "__main__":
    main()
