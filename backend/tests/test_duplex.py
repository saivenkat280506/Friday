"""
test_duplex.py — Phase 0 self-hearing & duplex controller tests (spec §3, §14)

Run:  pytest backend/tests/test_duplex.py -v
      python backend/tests/test_duplex.py  (standalone)

Tests:
1. Self-hearing — planted TTS sentence must never become a command
2. Silence — no transcript during tail/TTS window
3. Fuzzy echo — Whisper rephrasing of TTS still dropped
4. Tail — 500ms acoustic tail blocks listening
5. Hard mute — can_listen() is False during TTS
6. Barge-in — "stop"/"friday" allowed through
7. Hallucination — amara.org etc. dropped
8. No false positive — real user command not dropped
9. Phantom exact — "thank you" etc.
10. Echo under load — multiple last_spoken entries

Spec requires: self-hearing test passes 10/10. This suite is the automated gate.
"""

import sys
import time
import unittest
from pathlib import Path

# Ensure backend is on path when run as `python backend/tests/test_duplex.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stt.duplex import DuplexController, duplex, TAIL_MS, FUZZY_THRESHOLD  # noqa: E402
from stt.filter import is_phantom_transcript, is_whisper_hallucination  # noqa: E402


class TestDuplexHardMute(unittest.TestCase):
    def setUp(self):
        self.d = DuplexController()

    def test_can_listen_true_when_idle(self):
        self.assertTrue(self.d.can_listen())

    def test_hard_mute_during_tts(self):
        self.d.notify_tts_start("Hello boss, working on it")
        self.assertFalse(self.d.can_listen(), "mic must not arm while TTS active")
        self.assertTrue(self.d.is_tts_active())
        self.assertFalse(self.d.is_in_tail())

    def test_tail_blocks_after_tts(self):
        self.d.notify_tts_start("It's 5:37 PM.")
        self.d.notify_tts_end()
        self.assertFalse(self.d.can_listen(), "tail must block immediately after TTS")
        self.assertTrue(self.d.is_in_tail())
        self.assertGreater(self.d.tail_remaining_ms(), 0)

    def test_listen_resumes_after_tail(self):
        self.d.set_tail_ms(300)  # minimum allowed
        self.d.notify_tts_start("Test")
        self.d.notify_tts_end()
        time.sleep(0.38)
        self.assertFalse(self.d.is_in_tail())
        self.assertTrue(self.d.can_listen())

    def test_vad_and_wake_follow_gate(self):
        self.d.notify_tts_start("Speaking")
        self.assertFalse(self.d.vad_should_arm())
        self.assertFalse(self.d.wake_should_arm())
        self.d.notify_tts_end()
        self.assertFalse(self.d.vad_should_arm(), "still in tail")
        self.d.set_tail_ms(300)
        time.sleep(0.38)
        self.assertTrue(self.d.vad_should_arm())
        self.assertTrue(self.d.wake_should_arm())


class TestEchoFilter(unittest.TestCase):
    def setUp(self):
        self.d = DuplexController()

    def test_exact_echo_dropped(self):
        self.d.notify_tts_start("It's 5:37 PM.")
        is_echo, matched = self.d.is_echo("It's 5:37 PM.")
        self.assertTrue(is_echo)

    def test_fuzzy_echo_dropped(self):
        # Whisper often rephrases / adds punctuation
        self.d.notify_tts_start("It's 5:37 PM on Friday, Boss.")
        # transcript without punctuation/case diff
        self.assertTrue(self.d.is_echo("its 537 pm on friday boss")[0])
        # partial
        self.assertTrue(self.d.is_echo("5:37 PM")[0])
        # lower case no period
        self.assertTrue(self.d.is_echo("it's 5:37 pm")[0])

    def test_last_two_spoken_tracked(self):
        self.d.notify_tts_start("First sentence here.")
        self.d.notify_tts_start("Second sentence there.")
        self.assertTrue(self.d.is_echo("First sentence here.")[0])
        self.assertTrue(self.d.is_echo("Second sentence there")[0])
        # deque maxlen 2 — first should still be there (2 entries)
        self.assertEqual(len(self.d.get_last_spoken()), 2)

    def test_non_echo_not_dropped(self):
        self.d.notify_tts_start("It's 5:37 PM.")
        self.assertFalse(self.d.is_echo("open chrome")[0])
        self.assertFalse(self.d.is_echo("what time is it")[0])
        self.assertFalse(self.d.is_echo("play music")[0])

    def test_tts_error_lines_dropped(self):
        self.d.notify_tts_start("Hello")
        # known error line should be treated as echo/phantom even if not last spoken
        self.assertTrue(self.d.is_echo("My language service isn't available right now.")[0])
        self.assertTrue(self.d.is_echo("The language service isn't available")[0])

    def test_echo_substring_match(self):
        self.d.notify_tts_start("Opening Chrome now, Boss.")
        self.assertTrue(self.d.is_echo("Opening Chrome")[0])
        self.assertTrue(self.d.is_echo("opening chrome now boss")[0])

    def test_should_drop_echo(self):
        self.d.notify_tts_start("It's 5:37 PM.")
        # must notify end but stay in tail — should still drop
        self.d.notify_tts_end()
        # during tail, even non-echo is dropped (half-duplex)
        drop, reason = self.d.should_drop_transcript("It's 5:37 PM.")
        self.assertTrue(drop)
        self.assertTrue("tail" in reason or "echo" in reason)
        # after tail, echo reason
        self.d.set_tail_ms(300)
        time.sleep(0.38)
        drop, reason = self.d.should_drop_transcript("It's 5:37 PM.")
        self.assertTrue(drop)
        self.assertIn("echo", reason)

    def test_short_transcript_not_echo(self):
        self.d.notify_tts_start("This is a longer spoken sentence for testing")
        # 1-2 char blips should not be considered echo
        self.assertFalse(self.d.is_echo("a")[0])
        self.assertFalse(self.d.is_echo("ok")[0])


class TestBargeIn(unittest.TestCase):
    def setUp(self):
        self.d = DuplexController()

    def test_barge_stop_allowed(self):
        self.d.notify_tts_start("It's 5:37 PM on Friday")
        # during TTS, "stop" must be allowed
        self.assertTrue(self.d.is_barge_in("stop"))
        self.assertTrue(self.d.is_barge_in("stop talking"))
        drop, reason = self.d.should_drop_transcript("stop")
        self.assertFalse(drop, "barge-in must not be dropped even during TTS")

    def test_barge_friday_allowed(self):
        self.d.notify_tts_start("Hello boss")
        self.assertTrue(self.d.is_barge_in("friday wait"))
        drop, reason = self.d.should_drop_transcript("hey friday stop")
        self.assertFalse(drop)

    def test_echo_is_not_barge(self):
        self.d.notify_tts_start("It's 5:37 PM.")
        self.assertFalse(self.d.is_barge_in("It's 5:37 PM."))

    def test_non_barge_dropped_during_tts(self):
        self.d.notify_tts_start("Speaking now")
        drop, _ = self.d.should_drop_transcript("open chrome")
        self.assertTrue(drop)
        self.assertFalse(self.d.is_barge_in("open chrome"))


class TestPhantomAndHallucination(unittest.TestCase):
    def test_hallucination_markers(self):
        self.assertTrue(is_whisper_hallucination("subtitles by the amara.org community"))
        self.assertTrue(is_whisper_hallucination("thanks for watching please subscribe"))
        self.assertFalse(is_whisper_hallucination("open chrome"))

    def test_phantom_exact(self):
        self.assertTrue(is_phantom_transcript("thank you"))
        self.assertTrue(is_phantom_transcript("thanks for watching"))
        self.assertFalse(is_phantom_transcript("what time is it"))

    def test_phantom_fuzzy_echo(self):
        # filter's fuzzy echo should catch rephrased TTS
        self.assertTrue(is_phantom_transcript("It's 5:37 PM.", last_assistant="It's 5:37 PM on Friday, Boss."))
        # but not false positive
        self.assertFalse(is_phantom_transcript("open youtube", last_assistant="It's 5:37 PM"))

    def test_should_drop_hallucination_via_duplex(self):
        d = DuplexController()
        drop, reason = d.should_drop_transcript("subtitles by the amara.org community")
        self.assertTrue(drop)
        self.assertEqual(reason, "hallucination")

    def test_real_command_not_dropped(self):
        d = DuplexController()
        for cmd in ["open chrome", "play music", "what's on my screen", "search for laptops"]:
            drop, _ = d.should_drop_transcript(cmd)
            self.assertFalse(drop, f"real command should not be dropped: {cmd}")


class TestSelfHearingIntegration(unittest.TestCase):
    """
    Spec §3.7: "Script: Friday says a unique sentence. Mic stays open.
    Pass = zero commands from that sentence. Fail = any graph run."

    Simulates 10 consecutive runs with unique planted sentences.
    """

    def _plant_and_capture(self, planted: str, captured: str) -> bool:
        """
        Returns True if captured transcript should be IGNORED (pass),
        False if it would spawn a command (fail).
        """
        d = DuplexController()
        d.notify_tts_start(planted)
        d.notify_tts_end()
        # simulate mic capturing during tail + shortly after
        # During tail, everything is dropped
        drop_tail, _ = d.should_drop_transcript(captured)
        if drop_tail:
            return True
        # After tail, echo should still be dropped
        d.set_tail_ms(300)
        time.sleep(0.38)
        drop_echo, _ = d.should_drop_transcript(captured)
        return drop_echo

    def test_self_hearing_10_times(self):
        uniques = [
            "It's 5:37 PM.",
            "Opening Chrome now, Boss.",
            "The test is still red. Want me to read the log?",
            "Spinning up music, Boss.",
            "It's Friday evening, time to relax.",
            "Done, Boss. Click executed.",
            "Volume at 80 percent, Boss.",
            "Here are the latest headlines, Boss.",
            "Searching for laptops now.",
            "You have a reminder for your meeting.",
        ]
        for planted in uniques:
            captured = planted  # mic hears exactly what TTS said
            self.assertTrue(
                self._plant_and_capture(planted, captured),
                f"Self-hearing failed for planted={planted!r}",
            )
            # also rephrased capture (Whisper variation)
            rephrased = planted.lower().replace(",", "").strip()
            self.assertTrue(
                self._plant_and_capture(planted, rephrased),
                f"Self-hearing failed for rephrased={rephrased!r}",
            )

    def test_self_hearing_with_filter_integration(self):
        # End-to-end via filter.py entry point
        planted = "My language service isn't available right now."
        self.assertTrue(is_phantom_transcript(planted, last_assistant=planted))
        self.assertTrue(is_phantom_transcript("The language service isn't available", last_assistant=planted))


class TestTailTiming(unittest.TestCase):
    def test_tail_range(self):
        self.assertGreaterEqual(TAIL_MS, 300)
        self.assertLessEqual(TAIL_MS, 600)
        self.assertAlmostEqual(TAIL_MS, 500, delta=150)

    def test_tail_configurable(self):
        d = DuplexController()
        d.set_tail_ms(300)
        self.assertEqual(d.get_tail_ms(), 300)
        d.set_tail_ms(700)  # clamped to max
        self.assertLessEqual(d.get_tail_ms(), 600)
        d.set_tail_ms(100)  # clamped to min
        self.assertGreaterEqual(d.get_tail_ms(), 300)


if __name__ == "__main__":
    # standalone run without pytest
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # Self-hearing gate report
    if result.wasSuccessful():
        print("\n[PASS] Phase 0 self-hearing gate: 10/10 — ready for Phase 1")
    else:
        print("\n[FAIL] Self-hearing gate failed — do not proceed to always-on vision")
    sys.exit(0 if result.wasSuccessful() else 1)
