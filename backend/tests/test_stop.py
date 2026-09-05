"""
test_stop.py — Phase 4 Stop Controller & Permission Engine tests

Run: python backend/tests/test_stop.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from executor.stop import stop_all, reset_for_test
from executor.permission import (
    permission_gate,
    is_sensitive_input,
    REQUIRE_CONFIRM,
    BLOCK_ALWAYS,
    ALLOW_ALWAYS,
)


class TestStopController(unittest.TestCase):
    def setUp(self):
        reset_for_test()
    @patch("executor.stop._stop_tts")
    @patch("executor.stop._cancel_tasks")
    @patch("executor.stop._reset_flags")
    @patch("executor.stop._broadcast_stopped")
    def test_stop_all_calls_subsystems(self, mock_bcast, mock_flags, mock_cancel, mock_tts):
        stop_all("test_reason")
        mock_tts.assert_called_once()
        mock_cancel.assert_called_once()
        mock_flags.assert_called_once()
        mock_bcast.assert_called_once()

    @patch("executor.stop._stop_tts")
    @patch("executor.stop._cancel_tasks")
    @patch("executor.stop._reset_flags")
    @patch("executor.stop._broadcast_stopped")
    def test_stop_all_debounced(self, mock_bcast, mock_flags, mock_cancel, mock_tts):
        stop_all("test_1")
        stop_all("test_2")  # immediately called, within debounce window
        self.assertEqual(mock_tts.call_count, 1)


class TestPermissionEngine(unittest.TestCase):
    def test_blocked_tools(self):
        for tool in BLOCK_ALWAYS:
            res = permission_gate(tool)
            self.assertTrue(res.blocked)
            self.assertFalse(res.allowed)
            self.assertFalse(res.requires_confirm)

    def test_require_confirm_tools(self):
        for tool in ["delete_file", "shutdown", "restart"]:
            res = permission_gate(tool, {"path": "/tmp/important.txt"})
            self.assertTrue(res.requires_confirm)
            self.assertFalse(res.allowed)
            self.assertIn("confirm", res.prompt.lower())

    def test_confirmed_bypasses(self):
        res = permission_gate("delete_file", {"path": "/tmp/test.txt"}, confirmed=True)
        self.assertTrue(res.allowed)
        self.assertFalse(res.requires_confirm)
        self.assertFalse(res.blocked)

    def test_allowed_tools(self):
        for tool in ALLOW_ALWAYS:
            res = permission_gate(tool)
            self.assertTrue(res.allowed)
            self.assertFalse(res.requires_confirm)
            self.assertFalse(res.blocked)

    def test_whatsapp_permission(self):
        # Navigation / search only does not require confirm
        search_res = permission_gate("send_whatsapp_message", {"contact": "Mum"})
        self.assertTrue(search_res.allowed)
        self.assertFalse(search_res.requires_confirm)

        # Actual send with message requires confirm unless confirmed
        send_res = permission_gate("send_whatsapp_message", {"contact": "Mum", "message": "Hello"})
        self.assertTrue(send_res.requires_confirm)
        self.assertFalse(send_res.allowed)
        self.assertIn("confirm", send_res.prompt.lower())

        # When user confirmed, allowed to send
        ok_res = permission_gate("send_whatsapp_message", {"contact": "Mum", "message": "Hello"}, confirmed=True)
        self.assertTrue(ok_res.allowed)
        self.assertFalse(ok_res.requires_confirm)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 4 stop & permission gate — all tests passed")
    else:
        print("\n[FAIL] Phase 4 stop & permission gate failed")
    sys.exit(0 if result.wasSuccessful() else 1)
