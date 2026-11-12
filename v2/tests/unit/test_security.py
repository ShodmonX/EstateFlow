import unittest
from v2.shared.security import (
    issue_actor,
    verify_actor,
    verify_webapp,
    sign_webapp,
    guard_fake_only,
)


class SecurityTests(unittest.TestCase):
    def test_token_tampering_expiry_and_future_issued_rejected(self):
        token = issue_actor("synthetic-user", ["user"], "synthetic-secret", ttl=10, now=100)
        self.assertEqual(verify_actor(token, "synthetic-secret", now=105)["id"], "synthetic-user")
        for at in (90, 111):
            with self.assertRaises(ValueError):
                verify_actor(token, "synthetic-secret", now=at)
        with self.assertRaises(ValueError):
            verify_actor(token + "x", "synthetic-secret", now=105)

    def test_webapp_signed_identity_with_bounded_clock_skew(self):
        data = sign_webapp(7001, "synthetic-not-a-real-bot-token", now=100)
        self.assertEqual(
            verify_webapp(data, "synthetic-not-a-real-bot-token", now=100)["id"], "7001"
        )
        for at in (0, 90000):
            with self.assertRaises(ValueError):
                verify_webapp(data, "synthetic-not-a-real-bot-token", now=at)
        with self.assertRaises(ValueError):
            verify_webapp(data, "different-key", now=100)

    def test_real_provider_configuration_fails_closed(self):
        guard_fake_only({"EFV2_PROVIDER_MODE": "FAKE_ONLY"})
        for env in (
            {"EFV2_PROVIDER_MODE": "LIVE"},
            {"OPENROUTER_API_KEY": "SYNTHETIC_FORBIDDEN"},
            {"TELEGRAM_SESSION_DIR": "/synthetic/session"},
            {"R2_ENDPOINT_URL": "https://synthetic.invalid"},
        ):
            with self.assertRaises(ValueError):
                guard_fake_only(env)
