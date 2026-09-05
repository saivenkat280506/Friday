"""
test_world.py — Phase 2 World Snapshot tests (mocked, no system calls)

Run: pytest backend/tests/test_world.py -v
     python backend/tests/test_world.py
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.world import (
    WorldSnapshot,
    WorldState,
)
from perception.verify import _app_matches, _normalise_app_name


class TestWorldSnapshot(unittest.TestCase):
    def test_snapshot_defaults(self):
        s = WorldSnapshot()
        self.assertEqual(s.app, "")
        self.assertEqual(s.window_title, "")
        self.assertIsNone(s.screen_b64)

    def test_age(self):
        s = WorldSnapshot(captured_at=time.monotonic() - 2.0)
        self.assertGreater(s.age_s(), 1.9)
        self.assertTrue(s.is_stale(max_age_s=1.0))
        self.assertFalse(s.is_stale(max_age_s=5.0))

    def test_context_string(self):
        s = WorldSnapshot(app_display="Google Chrome", window_title="GitHub - Friday")
        ctx = s.to_context_string()
        self.assertIn("Google Chrome", ctx)
        self.assertIn("GitHub - Friday", ctx)

    def test_context_string_empty(self):
        s = WorldSnapshot()
        self.assertEqual(s.to_context_string(), "Desktop")

    def test_screen_age(self):
        s = WorldSnapshot()
        self.assertEqual(s.screen_age_s(), float("inf"))
        s.screen_captured_at = time.monotonic() - 1.0
        self.assertGreater(s.screen_age_s(), 0.9)


class TestWorldState(unittest.TestCase):
    def test_get_returns_snapshot(self):
        ws = WorldState()
        snap = ws.get()
        self.assertIsInstance(snap, WorldSnapshot)

    def test_update_replaces_snapshot(self):
        ws = WorldState()
        ws._update("chrome", "Google Chrome", "GitHub - Friday")
        snap = ws.get()
        self.assertEqual(snap.app, "chrome")
        self.assertEqual(snap.app_display, "Google Chrome")
        self.assertEqual(snap.window_title, "GitHub - Friday")

    def test_screen_rate_limited(self):
        ws = WorldState()
        # Simulate a recent capture
        ws._snap.screen_b64 = "abc123"
        ws._snap.screen_captured_at = time.monotonic() - 0.5  # 500ms ago, < 3s
        # Should return cached, not re-capture
        result = ws.capture_screen()
        self.assertEqual(result, "abc123")

    def test_screen_capture_when_stale(self):
        ws = WorldState()
        ws._snap.screen_b64 = "old_frame"
        ws._snap.screen_captured_at = time.monotonic() - 10.0  # old
        with patch("perception.world._capture_screen_macos", return_value="new_frame"):
            result = ws.capture_screen()
        self.assertEqual(result, "new_frame")


class TestAppMatches(unittest.TestCase):
    def test_exact_substring(self):
        self.assertTrue(_app_matches("chrome", "Google Chrome", "com.google.Chrome"))
        self.assertTrue(_app_matches("safari", "Safari", "com.apple.Safari"))

    def test_case_insensitive(self):
        self.assertTrue(_app_matches("Chrome", "google chrome", ""))
        self.assertTrue(_app_matches("FINDER", "Finder", "com.apple.finder"))

    def test_alias_match(self):
        self.assertTrue(_app_matches("vscode", "Visual Studio Code", "com.microsoft.VSCode"))
        self.assertTrue(_app_matches("code", "Visual Studio Code", ""))

    def test_no_match(self):
        self.assertFalse(_app_matches("calendar", "Google Chrome", ""))
        self.assertFalse(_app_matches("spotify", "Finder", "com.apple.finder"))

    def test_empty_expected_always_passes(self):
        self.assertTrue(_app_matches("", "Any App", "any.bundle"))

    def test_word_match(self):
        self.assertTrue(_app_matches("terminal", "Terminal", "com.apple.Terminal"))


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 2 world snapshot gate — all tests passed")
    else:
        print("\n[FAIL] World snapshot gate failed")
    sys.exit(0 if result.wasSuccessful() else 1)
