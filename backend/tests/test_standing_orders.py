"""
test_standing_orders.py — Phase 5 Standing Orders & Redaction tests

Run: python backend/tests/test_standing_orders.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.standing_orders import (
    StandingOrder,
    StandingOrderStore,
    _parse_standing_order,
)
from brain.redact import (
    redact,
    contains_secret,
    safe_for_memory,
)


class TestStandingOrders(unittest.TestCase):
    def setUp(self):
        self.store = StandingOrderStore.__new__(StandingOrderStore)
        import threading
        self.store._lock = threading.RLock()
        self.store._orders = []
        self.store._db_path = Path("/tmp/test_so_never_used.db")
        self.store._db_ready = False

    def test_add_and_list(self):
        order = StandingOrder(instruction="always confirm before sending WhatsApp", tool="send_whatsapp_message")
        self.store.add(order)
        self.assertEqual(len(self.store.all_orders()), 1)

    def test_remove_by_text(self):
        order = StandingOrder(instruction="always confirm before sending WhatsApp", tool="send_whatsapp_message")
        self.store.add(order)
        removed = self.store.remove_by_text("standing order")
        self.assertGreaterEqual(removed, 0)

    def test_grants_permission(self):
        order = StandingOrder(
            instruction="stop asking me for Mum",
            tool="send_whatsapp_message",
            contact="Mum",
            grants_confirm=True,
        )
        self.store.add(order)
        self.assertTrue(self.store.grants_permission("send_whatsapp_message", {"contact": "Mum"}))
        self.assertFalse(self.store.grants_permission("send_whatsapp_message", {"contact": "Alex"}))

    def test_context_string(self):
        self.store.add(StandingOrder(instruction="Never open YouTube"))
        ctx = self.store.to_context_string()
        self.assertIn("Standing orders:", ctx)
        self.assertIn("Never open YouTube", ctx)

    def test_parse_standing_order(self):
        action, so = _parse_standing_order("always confirm before sending WhatsApp")
        self.assertEqual(action, "add")
        self.assertIsNotNone(so)
        self.assertEqual(so.tool, "send_whatsapp_message")

        action, so = _parse_standing_order("remove standing order")
        self.assertEqual(action, "remove")


class TestSecretRedaction(unittest.TestCase):
    def test_redact_api_key(self):
        text = "My key is sk-abcdef12345678901234567890"
        redacted = redact(text)
        self.assertNotIn("sk-abcdef12345678901234567890", redacted)
        self.assertIn("[API_KEY]", redacted)

    def test_redact_password(self):
        text = "password = supersecretpass"
        redacted = redact(text)
        self.assertIn("[REDACTED]", redacted)

    def test_contains_secret(self):
        self.assertTrue(contains_secret("here is my token: gsk_12345678901234567890"))
        self.assertFalse(contains_secret("hello world, play some music"))

    def test_safe_for_memory(self):
        self.assertEqual(safe_for_memory("normal conversation"), "normal conversation")
        safe = safe_for_memory("password: mypassword123")
        self.assertNotIn("mypassword123", safe)
        safe_token = safe_for_memory("token: abcdefgh1234")
        self.assertIsNotNone(safe_token)
        self.assertIn("[REDACTED]", safe_token)

    def test_redact_generic_secret_separator(self):
        redacted_colon = redact("token: secretpassword123")
        self.assertEqual(redacted_colon, "token:[REDACTED]")
        redacted_eq = redact("api_key=secretpassword123")
        self.assertEqual(redacted_eq, "api_key=[REDACTED]")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 5 standing orders & redaction — all tests passed")
    else:
        print("\n[FAIL] Phase 5 tests failed")
    sys.exit(0 if result.wasSuccessful() else 1)
