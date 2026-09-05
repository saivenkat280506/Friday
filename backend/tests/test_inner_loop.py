"""
test_inner_loop.py — Phase 3 Agenda + Attention Policy + Inner Loop tests

Run: pytest backend/tests/test_inner_loop.py -v
     python backend/tests/test_inner_loop.py
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.agenda import AgendaStore, Goal, TriggerType, _goal_is_triggered
from brain.attention import AttentionPolicy, SpeakContext


# ────────────────────────────────────────────────────────────────────────────
# Agenda tests
# ────────────────────────────────────────────────────────────────────────────

class TestGoalTriggers(unittest.TestCase):
    def _world(self, app="Google Chrome", title="GitHub"):
        from perception.world import WorldSnapshot
        return WorldSnapshot(app_display=app, app="Chrome", window_title=title, captured_at=time.monotonic())

    def test_once_trigger_fires(self):
        g = Goal(description="Hello", trigger=TriggerType.ONCE)
        self.assertTrue(_goal_is_triggered(g, None))

    def test_fired_goal_does_not_fire(self):
        g = Goal(description="Hello", trigger=TriggerType.ONCE, fired=True)
        self.assertFalse(_goal_is_triggered(g, None))

    def test_expired_goal_does_not_fire(self):
        g = Goal(description="Hello", trigger=TriggerType.ONCE, expires_at=time.time() - 1.0)
        self.assertFalse(_goal_is_triggered(g, None))

    def test_cooldown_suppresses(self):
        g = Goal(description="Hello", trigger=TriggerType.ONCE, last_fired_at=time.time() - 5.0)
        # FIRE_COOLDOWN_S = 30 — 5s ago is within cooldown
        self.assertFalse(_goal_is_triggered(g, None))

    def test_app_trigger_matches(self):
        from perception.world import WorldSnapshot
        world = self._world(app="Chrome", title="GitHub")
        g = Goal(description="Switch", trigger=TriggerType.APP, trigger_value="github")
        # App trigger checks title too
        self.assertTrue(_goal_is_triggered(g, world))

    def test_app_trigger_no_match(self):
        world = self._world(app="Finder", title="Desktop")
        g = Goal(description="Switch", trigger=TriggerType.APP, trigger_value="calendar")
        self.assertFalse(_goal_is_triggered(g, world))

    def test_keyword_trigger_matches(self):
        world = self._world(title="ERROR: build failed in friday_graph.py")
        g = Goal(description="Build failed!", trigger=TriggerType.KEYWORD, trigger_value="error,build failed")
        self.assertTrue(_goal_is_triggered(g, world))

    def test_keyword_trigger_no_match(self):
        world = self._world(title="Normal work")
        g = Goal(description="Build failed!", trigger=TriggerType.KEYWORD, trigger_value="error,build failed")
        self.assertFalse(_goal_is_triggered(g, world))

    def test_manual_never_auto_fires(self):
        g = Goal(description="Do it", trigger=TriggerType.MANUAL)
        self.assertFalse(_goal_is_triggered(g, None))

    def test_timezone_aware_iso_trigger(self):
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        g = Goal(description="Timezone test", trigger=TriggerType.TIME, trigger_value=now_iso)
        self.assertTrue(_goal_is_triggered(g, None))


class TestAgendaStore(unittest.TestCase):
    def _store(self) -> AgendaStore:
        # In-memory store (disable DB)
        store = AgendaStore.__new__(AgendaStore)
        import threading
        store._lock = threading.RLock()
        store._goals = []
        store._db_path = Path("/tmp/test_agenda_never_used.db")
        store._db_ready = False
        return store

    def test_add_and_list(self):
        s = self._store()
        g = Goal(description="Test goal", trigger=TriggerType.ONCE)
        s.add_goal(g)
        self.assertEqual(len(s.all_goals()), 1)

    def test_mark_fired_removes_once(self):
        s = self._store()
        g = Goal(description="Test", trigger=TriggerType.ONCE)
        s.add_goal(g)
        s.mark_fired(g.id)
        self.assertEqual(len(s.all_goals()), 0)

    def test_get_pending_goals(self):
        s = self._store()
        g1 = Goal(description="Go!", trigger=TriggerType.ONCE)
        g2 = Goal(description="Manual", trigger=TriggerType.MANUAL)
        s.add_goal(g1)
        s.add_goal(g2)
        pending = s.get_pending_goals(None)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, g1.id)

    def test_remove_goal(self):
        s = self._store()
        g = Goal(description="Remove me", trigger=TriggerType.ONCE)
        s.add_goal(g)
        removed = s.remove_goal(g.id)
        self.assertTrue(removed)
        self.assertEqual(len(s.all_goals()), 0)

    def test_clear(self):
        s = self._store()
        for i in range(3):
            s.add_goal(Goal(description=f"Goal {i}", trigger=TriggerType.ONCE))
        s.clear()
        self.assertEqual(len(s.all_goals()), 0)


# ────────────────────────────────────────────────────────────────────────────
# Attention Policy tests
# ────────────────────────────────────────────────────────────────────────────

class TestAttentionPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = AttentionPolicy()
        self.policy.reset_for_test()

    def _ctx(self, urgent=False, content="", typing_ago=None):
        now = time.monotonic()
        typing_at = (now - typing_ago) if typing_ago else 0.0
        return SpeakContext(urgent=urgent, content=content, last_typing_at=typing_at)

    def test_allows_by_default(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            self.assertTrue(self.policy.should_speak(self._ctx()))

    def test_blocked_during_listening(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=True), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            self.assertFalse(self.policy.should_speak(self._ctx()))

    def test_blocked_during_tts(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=True):
            self.assertFalse(self.policy.should_speak(self._ctx()))

    def test_flow_state_blocks(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            # Typed 3 seconds ago — in flow
            self.assertFalse(self.policy.should_speak(self._ctx(typing_ago=3.0)))

    def test_urgent_bypasses_flow(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            # Urgent — should bypass flow state
            self.assertTrue(self.policy.should_speak(self._ctx(urgent=True, typing_ago=3.0)))

    def test_rate_limited_after_speak(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            self.assertTrue(self.policy.should_speak(self._ctx()))
            self.policy.record_spoke("hello")
            # Next speak should be rate-limited
            self.assertFalse(self.policy.should_speak(self._ctx()))

    def test_repeat_suppressed(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=True), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            self.policy.record_spoke("build failed")
            # Same content within 30s should be detected as repeat
            self.assertTrue(self.policy._is_repeat("build failed"))
            # Manually set last spoke far back to bypass rate limit
            self.policy._last_spoke_at = time.monotonic() - 400  # > 5 min
            # should_speak should still block because of repeat suppression
            ctx = self._ctx(content="build failed")
            self.assertFalse(self.policy.should_speak(ctx))

    def test_presence_blocks(self):
        with patch("brain.attention.AttentionPolicy._presence_allows", return_value=False), \
             patch("brain.attention.AttentionPolicy._listening_or_processing", return_value=False), \
             patch("brain.attention.AttentionPolicy._tts_active", return_value=False):
            self.assertFalse(self.policy.should_speak(self._ctx()))

    def test_time_until_allowed(self):
        self.policy.record_spoke()
        remaining = self.policy.time_until_allowed_s()
        self.assertGreater(remaining, 290)  # ~5 min


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 3 inner loop gate — all tests passed")
    else:
        print("\n[FAIL] Inner loop gate failed")
    sys.exit(0 if result.wasSuccessful() else 1)
