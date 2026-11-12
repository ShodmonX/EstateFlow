from fastapi.testclient import TestClient
from v2.apps.api import create_app
from v2.shared.security import issue_actor, sign_webapp

KEY = "synthetic-test-key-at-least-32-bytes"
BOT = "synthetic-fake-bot-token"


class Gateway:
    def __init__(self):
        self.commands = []

    def request(self, owner, method, path, token, body=None, params=None):
        if method == "POST":
            self.commands.append((owner, body))
            return {
                "operation_id": body["operation_id"],
                "status": "accepted",
                "owner": owner,
            }
        if path == "/search":
            return {"items": [], "metadata": {"total": 0}, "mode": "FAKE_ONLY"}
        return []


def test_webapp_cannot_spoof_signed_subject_or_bypass_signature():
    gateway = Gateway()
    client = TestClient(create_app(gateway=gateway, key=KEY, bot_key=BOT))
    assert client.get("/api/webapp/me", headers={"X-Telegram-User-Id": "123"}).status_code == 401
    response = client.post("/api/webapp/auth", json={"init_data": sign_webapp(123, BOT)})
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/api/webapp/me", headers=headers).json()["user_id"] == "123"
    saved = client.post(
        "/api/webapp/filters",
        headers=headers,
        json={"name": "Synthetic", "criteria": {"max_price": 100}},
    )
    assert saved.status_code == 202
    assert gateway.commands[0][0] == "notifications"
    assert gateway.commands[0][1]["arguments"]["user_id"] == "123"


def test_admin_denies_user_token_and_real_session_starts():
    client = TestClient(create_app("admin_api", Gateway(), KEY, BOT))
    user = {"Authorization": "Bearer " + issue_actor("user", ["user"], KEY)}
    admin = {"Authorization": "Bearer " + issue_actor("admin", ["admin"], KEY)}
    assert client.get("/api/admin/release/flags", headers=user).status_code == 403
    assert client.post("/api/admin/telegram/auth/start", headers=admin, json={}).status_code == 409


def test_ui_static_artifacts_and_operation_error_are_explicit():
    client = TestClient(create_app(gateway=Gateway(), key=KEY, bot_key=BOT))
    page = client.get("/")
    assert page.status_code == 200 and "FAKE ONLY" in page.text
    assert client.get("/app.js").status_code == 200
    assert "textContent" in client.get("/app.js").text
    assert client.get("/api/search/announcements").status_code == 401
