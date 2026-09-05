"""
test_presence.py — Phase 1 Presence Mode tests

Run: pytest backend/tests/test_presence.py -v
     python backend/tests/test_presence.py

Tests:
1.  Default mode is RESIDENT
2.  set_mode_sync SLEEP blocks mic
3.  set_mode_sync QUIET — continuous disallowed, wake ok
4.  set_mode_sync RESIDENT — all gates open
5.  SLEEP blocks can_arm_mic
6.  classify_presence_intent — timed sleep
7.  classify_presence_intent — phrase-based sleep
8.  classify_presence_intent — quiet mode
9.  classify_presence_intent — resident/wake
10. Timed sleep auto-expires (mocked monotonic)
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.presence import (
    PresenceMode,
    PresenceState,
    classify_presence_intent,
)


class TestPresenceModeDefault(unittest.TestCase):
    def test_default_resident(self):
        p = PresenceState()
        self.assertEqual(p.get_mode(), PresenceMode.RESIDENT)
        self.assertTrue(p.is_resident())
        self.assertFalse(p.is_sleeping())
        self.assertFalse(p.is_quiet())

    def test_can_arm_mic_default(self):
        p = PresenceState()
        self.assertTrue(p.can_arm_mic())
        self.assertTrue(p.can_listen_continuous())
        self.assertTrue(p.can_speak_unsolicited())


class TestPresenceModeSleep(unittest.TestCase):
    def setUp(self):
        self.p = PresenceState()
        self.p.set_mode_sync(PresenceMode.SLEEP, reason="test")

    def test_is_sleeping(self):
        self.assertTrue(self.p.is_sleeping())
        self.assertFalse(self.p.is_resident())
        self.assertFalse(self.p.is_quiet())

    def test_mic_blocked(self):
        self.assertFalse(self.p.can_arm_mic())
        self.assertFalse(self.p.can_listen_continuous())
        self.assertFalse(self.p.can_speak_unsolicited())


class TestPresenceModeQuiet(unittest.TestCase):
    def setUp(self):
        self.p = PresenceState()
        self.p.set_mode_sync(PresenceMode.QUIET, reason="test")

    def test_is_quiet(self):
        self.assertTrue(self.p.is_quiet())
        self.assertFalse(self.p.is_sleeping())
        self.assertFalse(self.p.is_resident())

    def test_no_continuous_listen(self):
        self.assertFalse(self.p.can_listen_continuous())
        self.assertFalse(self.p.can_speak_unsolicited())

    def test_arm_mic_allowed_quiet(self):
        # QUIET allows wake-word (mic can arm), just not continuous
        self.assertTrue(self.p.can_arm_mic())


class TestPresenceModeTransition(unittest.TestCase):
    def test_sleep_then_resident(self):
        p = PresenceState()
        p.set_mode_sync(PresenceMode.SLEEP)
        self.assertTrue(p.is_sleeping())
        p.set_mode_sync(PresenceMode.RESIDENT)
        self.assertTrue(p.is_resident())
        self.assertTrue(p.can_arm_mic())
        self.assertTrue(p.can_speak_unsolicited())

    def test_quiet_then_sleep(self):
        p = PresenceState()
        p.set_mode_sync(PresenceMode.QUIET)
        p.set_mode_sync(PresenceMode.SLEEP)
        self.assertTrue(p.is_sleeping())
        self.assertFalse(p.can_arm_mic())


class TestTimedSleepExpiry(unittest.TestCase):
    def test_timed_sleep_auto_expires(self):
        p = PresenceState()
        p.set_mode_sync(PresenceMode.SLEEP, duration_s=0.3)  # 300ms
        self.assertTrue(p.is_sleeping())
        time.sleep(0.35)
        # get_mode() checks monotonic and auto-expires
        mode = p.get_mode()
        self.assertEqual(mode, PresenceMode.RESIDENT, "Timed sleep should auto-expire to RESIDENT")
        self.assertTrue(p.can_arm_mic())

    def test_sleep_remaining_decreases(self):
        p = PresenceState()
        p.set_mode_sync(PresenceMode.SLEEP, duration_s=60.0)
        r1 = p.sleep_remaining_s()
        time.sleep(0.05)
        r2 = p.sleep_remaining_s()
        self.assertGreater(r1, r2)
        self.assertGreater(r2, 59.0)


class TestClassifyPresenceIntent(unittest.TestCase):
    def _classify(self, text: str) -> tuple[PresenceMode | None, float | None]:
        return classify_presence_intent(text)

    def test_timed_sleep_minutes(self):
        mode, dur = self._classify("give me 30 minutes")
        self.assertEqual(mode, PresenceMode.SLEEP)
        self.assertAlmostEqual(dur, 1800, delta=1)

    def test_timed_sleep_hour(self):
        mode, dur = self._classify("give me an hour")
        self.assertEqual(mode, PresenceMode.SLEEP)
        self.assertAlmostEqual(dur, 3600, delta=1)

    def test_timed_sleep_one_hour(self):
        mode, dur = self._classify("give me 1 hour")
        self.assertEqual(mode, PresenceMode.SLEEP)
        self.assertAlmostEqual(dur, 3600, delta=1)

    def test_go_to_sleep(self):
        mode, dur = self._classify("go to sleep")
        self.assertEqual(mode, PresenceMode.SLEEP)
        self.assertIsNone(dur)

    def test_leave_me_alone(self):
        mode, dur = self._classify("leave me alone")
        self.assertEqual(mode, PresenceMode.SLEEP)
        self.assertIsNone(dur)

    def test_stop_listening(self):
        mode, _ = self._classify("stop listening")
        self.assertEqual(mode, PresenceMode.SLEEP)

    def test_quiet_mode(self):
        mode, dur = self._classify("quiet mode")
        self.assertEqual(mode, PresenceMode.QUIET)
        self.assertIsNone(dur)

    def test_just_watch(self):
        mode, _ = self._classify("just watch")
        self.assertEqual(mode, PresenceMode.QUIET)

    def test_i_am_back(self):
        mode, _ = self._classify("I'm back")
        self.assertEqual(mode, PresenceMode.RESIDENT)

    def test_come_back(self):
        mode, _ = self._classify("come back")
        self.assertEqual(mode, PresenceMode.RESIDENT)

    def test_resume_listening(self):
        mode, _ = self._classify("start listening again")
        self.assertEqual(mode, PresenceMode.RESIDENT)

    def test_no_presence_command(self):
        mode, _ = self._classify("open chrome")
        self.assertIsNone(mode)
        mode, _ = self._classify("what time is it")
        self.assertIsNone(mode)
        mode, _ = self._classify("play music")
        self.assertIsNone(mode)

    def test_listener_called_on_change(self):
        p = PresenceState()
        received = []
        p.add_listener(lambda m: received.append(m))
        p.set_mode_sync(PresenceMode.SLEEP)
        p.set_mode_sync(PresenceMode.QUIET)
        p.set_mode_sync(PresenceMode.RESIDENT)
        self.assertEqual(received, [PresenceMode.SLEEP, PresenceMode.QUIET, PresenceMode.RESIDENT])


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 1 presence mode gate — all tests passed")
    else:
        print("\n[FAIL] Presence mode gate failed")
    sys.exit(0 if result.wasSuccessful() else 1)
