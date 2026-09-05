"""
duplex.py — Phase 0 Echo / Duplex Controller
=============================================

Central authority for "she must not hear herself".

Responsibilities:
1. Hard mute during TTS — VAD/wake/STT refuse to arm while TTS is active
2. Never call sd.stop() while mic is open (guard via runtime flags)
3. Acoustic tail 300-600ms after TTS ends before listening resumes
4. Playback echo filter — fuzzy match next transcript against last 1-2 TTS utterances
5. False-start VAD tuning helpers
6. Half-duplex policy + barge-in allow-list ("stop", "friday", "wait")
7. Self-hearing evaluation helpers

Thread-safe. No heavy deps (difflib only).

Usage:
    from stt.duplex import duplex
    duplex.notify_tts_start("It's 5:37 PM")
    if not duplex.can_listen():
        return  # mic gated
    if duplex.should_drop_transcript(transcript):
        return

"""

from __future__ import annotations

import re
import time
import threading
from collections import deque
from difflib import SequenceMatcher

# ── Tunables (spec §3) ────────────────────────────────────────────────────────

TAIL_MS: int = 500                       # acoustic tail after TTS (300-600ms spec)
TAIL_MS_MIN: int = 300
TAIL_MS_MAX: int = 600

FUZZY_THRESHOLD: float = 0.82            # SequenceMatcher ratio to consider echo
TOKEN_THRESHOLD: float = 0.80            # Jaccard token overlap fallback
MIN_ECHO_CHARS: int = 4                  # don't fuzzy-match 1-2 char blips

BARGE_PHRASES = ("stop", "friday", "wait", "cancel", "hey friday", "hold on")
LAST_SPOKEN_MAX = 2

# VAD tuning for MacBook mic (spec §3.5 false-start)
# 30ms frames: 8 frames = 240ms, 12 frames = 360ms. 3 frames (90ms) blips ignored.
VAD_MIN_SPEECH_FRAMES: int = 8           # 240ms sustained before arming capture
VAD_MIN_UTTERANCE_FRAMES: int = 12       # 360ms total speech before transcribe
VAD_BLIP_MAX_FRAMES: int = 3             # 90ms blips are noise

# Acoustic echo suppression (spec §3.3)
# On darwin, PortAudio doesn't expose CoreAudio VoiceProcessingIO / voice isolation.
# We implement half-duplex suppression: all mic frames during TTS+tail are dropped.
# If a future macOS voice-isolation capture is available, this hook will enable it.
ECHO_SUPPRESSION_MODE: str = "half_duplex"  # "half_duplex" | "voice_isolation" | "envelope_subtract"

# Known TTS error / system lines that must never become a command
# (matches voice_loop _clean_transcript junk + filter hallucination)
KNOWN_TTS_ERROR_LINES = {
    "my language service isn't available right now",
    "the language service isn't available",
    "language service isn't available",
    "i had trouble speaking that",
    "sorry i had trouble speaking that",
    "processing",
    "running system checks",
}

# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy matching."""
    if not text:
        return ""
    t = text.lower().strip()
    # keep alphanumerics and spaces only for fuzzy comparison
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _token_overlap(a: str, b: str) -> float:
    """Jaccard over tokens."""
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _is_near_match(transcript_norm: str, spoken_norm: str) -> bool:
    if len(transcript_norm) < MIN_ECHO_CHARS or len(spoken_norm) < MIN_ECHO_CHARS:
        return False
    # exact substring (word-boundary) — strongest signal
    if transcript_norm == spoken_norm:
        return True
    # transcript contained within last spoken, or vice versa
    if len(transcript_norm) >= 6:
        # check containment with boundary flexibility
        if transcript_norm in spoken_norm or spoken_norm in transcript_norm:
            return True
    # fuzzy sequence
    if _fuzzy_ratio(transcript_norm, spoken_norm) >= FUZZY_THRESHOLD:
        return True
    # token overlap (handles word reordering / partial)
    if _token_overlap(transcript_norm, spoken_norm) >= TOKEN_THRESHOLD:
        return True
    return False


# ── Controller ───────────────────────────────────────────────────────────────

class DuplexController:
    """
    Thread-safe duplex state.

    All time uses time.monotonic() to avoid wall-clock jumps.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tts_active: bool = False
        self._tts_start_mono: float | None = None
        self._tts_end_mono: float | None = None
        self._tail_ms: int = TAIL_MS
        self._last_spoken: deque[str] = deque(maxlen=LAST_SPOKEN_MAX)
        self._last_spoken_norm: deque[str] = deque(maxlen=LAST_SPOKEN_MAX)
        self._barge_phrases = tuple(p.lower() for p in BARGE_PHRASES)
        self._echo_suppression = ECHO_SUPPRESSION_MODE
        self._playback_envelope: list[float] = []  # RMS envelope during last TTS (for envelope_subtract mode)
        self._detect_mac_echo_capability()

    # ── Acoustic echo suppression (spec §3.3) ─────────────────────────────────

    def _detect_mac_echo_capability(self) -> None:
        """Probe macOS for voice isolation / echo-cancelled capture."""
        import platform
        import sys

        if platform.system().lower() != "darwin":
            self._echo_suppression = "half_duplex"
            return
        # PortAudio (sounddevice) on macOS does NOT expose
        # kAudioUnitSubType_VoiceProcessingIO or AVAudioSession voice isolation.
        # half_duplex is the safe default until a native CoreAudio stream is added.
        # Log once so the operator knows the mode.
        self._echo_suppression = "half_duplex"
        try:
            # Future hook: if we ever switch to `soundcard` or native AVFoundation,
            # detect voice isolation here and set to "voice_isolation".
            # For now, just note the platform.
            print(f"[Duplex] macOS detected ({platform.mac_ver()[0]}), "
                  f"echo suppression={self._echo_suppression} (half-duplex gate + tail)")
        except Exception:
            pass

    def get_echo_suppression_mode(self) -> str:
        with self._lock:
            return self._echo_suppression

    def should_suppress_frame(self, frame_rms: float = 0.0) -> bool:
        """
        Frame-level acoustic echo gate.
        Returns True if this mic frame should be dropped as likely speaker echo.

        Currently: True whenever TTS is active or in tail (half-duplex).
        Future: compare frame_rms against playback_envelope for envelope_subtract.
        """
        if self.is_tts_active() or self.is_in_tail():
            return True
        # envelope_subtract mode would inspect playback_envelope here
        return False

    def update_playback_envelope(self, rms_values: list[float]) -> None:
        """Store recent playback RMS envelope (for future envelope_subtract mode)."""
        with self._lock:
            self._playback_envelope = list(rms_values[-100:])

    # ── TTS lifecycle ───────────────────────────────────────────────────────

    def notify_tts_start(self, text: str = "") -> None:
        """Call when TTS generation/playback begins. Stores spoken text for echo filter."""
        now = time.monotonic()
        norm = _normalize(text) if text else ""
        with self._lock:
            self._tts_active = True
            self._tts_start_mono = now
            # don't overwrite end — tail checks use end time only after notify_tts_end
            if norm and len(norm) >= 2:
                # avoid duplicate consecutive entries
                if not self._last_spoken_norm or self._last_spoken_norm[-1] != norm:
                    self._last_spoken.append(text.strip())
                    self._last_spoken_norm.append(norm)
            # also mirror to runtime_state for UI / debugging
            try:
                from services.runtime_state import flags
                if text:
                    flags.last_assistant_response = text.strip()
            except Exception:
                pass

    def notify_tts_end(self) -> None:
        """Call when TTS playback fully ends (stream drained, _is_speaking -> False)."""
        now = time.monotonic()
        with self._lock:
            self._tts_active = False
            self._tts_end_mono = now
            self._tts_start_mono = None

    def set_tail_ms(self, ms: int) -> None:
        with self._lock:
            self._tail_ms = max(TAIL_MS_MIN, min(TAIL_MS_MAX, int(ms)))

    def get_tail_ms(self) -> int:
        with self._lock:
            return self._tail_ms

    # ── State queries ───────────────────────────────────────────────────────

    def is_tts_active(self) -> bool:
        """
        Ground truth: checks internal flag AND pocket_tts.is_tts_active() if available.
        Internal flag is authoritative for tail handling; pocket_tts is extra guard.
        """
        with self._lock:
            internal = self._tts_active
        # external check — may be True even if internal not yet set (race)
        try:
            from tts.pocket_tts import is_tts_active as tts_active_ext
            external = bool(tts_active_ext())
        except Exception:
            external = False
        return internal or external

    def is_in_tail(self) -> bool:
        """Acoustic tail window after TTS end."""
        with self._lock:
            if self._tts_end_mono is None:
                return False
            if self._tts_active:
                return False
            tail_s = self._tail_ms / 1000.0
            return (time.monotonic() - self._tts_end_mono) < tail_s

    def tail_remaining_ms(self) -> int:
        with self._lock:
            if self._tts_end_mono is None or self._tts_active:
                return 0
            elapsed = (time.monotonic() - self._tts_end_mono) * 1000
            remain = self._tail_ms - int(elapsed)
            return max(0, remain)

    def can_listen(self) -> bool:
        """
        Hard gate: mic/VAD/wake must NOT arm if this is False.
        Spec §3: "Make listen, VAD, and wake-word refuse to arm while TTS is generating or playing."
        Plus tail.
        """
        if self.is_tts_active():
            return False
        if self.is_in_tail():
            return False
        # also respect is_listening / processing flags? No — duplex is lower layer.
        # Higher layers (voice_loop) decide based on system state.
        return True

    def can_arm_listening(self) -> bool:
        """Alias for can_listen() — explicit name for VAD/wake checks."""
        return self.can_listen()

    # ── Echo filter ──────────────────────────────────────────────────────────

    def get_last_spoken(self) -> list[str]:
        with self._lock:
            return list(self._last_spoken)

    def get_last_spoken_norm(self) -> list[str]:
        with self._lock:
            return list(self._last_spoken_norm)

    def is_echo(self, transcript: str) -> tuple[bool, str]:
        """
        Playback echo filter — fuzzy match against last 1-2 spoken sentences.
        Returns (is_echo, matched_spoken).
        """
        if not transcript or not transcript.strip():
            return False, ""
        norm = _normalize(transcript)
        if len(norm) < MIN_ECHO_CHARS:
            return False, ""
        # check known TTS error lines first
        for err in KNOWN_TTS_ERROR_LINES:
            err_norm = _normalize(err)
            if _is_near_match(norm, err_norm):
                return True, err
        with self._lock:
            snapshots_norm = list(self._last_spoken_norm)
            snapshots_raw = list(self._last_spoken)
        for spoken_norm, spoken_raw in zip(snapshots_norm, snapshots_raw):
            if _is_near_match(norm, spoken_norm):
                return True, spoken_raw
        return False, ""

    def is_barge_in(self, transcript: str) -> bool:
        """
        Barge-in allow-list: human says "stop"/"friday"/"wait" over TTS.
        Only returns True if transcript contains a barge phrase AND is not an echo.
        This is the sole exception to half-duplex.
        """
        if not transcript:
            return False
        norm = _normalize(transcript)
        if not norm:
            return False
        # if it's an echo, it's not a barge-in
        is_echo, _ = self.is_echo(transcript)
        if is_echo:
            return False
        for phrase in self._barge_phrases:
            p_norm = _normalize(phrase)
            if p_norm in norm or norm in p_norm:
                return True
            # also token containment
            phrase_tokens = set(p_norm.split())
            trans_tokens = set(norm.split())
            if phrase_tokens & trans_tokens:
                # require exact token match for single-word barages
                if any(tok in trans_tokens for tok in phrase_tokens):
                    # but avoid false positive like "stopped" vs "stop" via fuzzy
                    if _fuzzy_ratio(p_norm, norm) >= 0.75 or p_norm in norm:
                        return True
        return False

    def should_drop_transcript(self, transcript: str) -> tuple[bool, str]:
        """
        Unified drop decision for voice_loop / STT.

        Returns (drop, reason). If drop is True, transcript must be discarded
        and never reach the graph.

        Drop reasons: "tts_active", "tail", "echo:<spoken>", "hallucination", "phantom"
        """
        if not transcript or not transcript.strip():
            return True, "empty"
        cleaned = transcript.strip()
        lowered = cleaned.lower()

        # 1. Hard mute during TTS (half-duplex) — unless it's a barge-in
        if self.is_tts_active():
            if self.is_barge_in(cleaned):
                return False, "barge-in"
            return True, "tts_active"

        # 2. Tail
        if self.is_in_tail():
            if self.is_barge_in(cleaned):
                return False, "barge-in-tail"
            return True, f"tail:{self.tail_remaining_ms()}ms"

        # 3. Phantom / hallucination (reuse filter logic if available)
        try:
            from stt.filter import is_whisper_hallucination, is_phantom_transcript
            if is_whisper_hallucination(cleaned):
                return True, "hallucination"
            # pass last_assistant for exact echo check; we also do fuzzy below
            # avoid double-counting — is_phantom_transcript already checks last_assistant
            # but we want fuzzy echo here too
            if is_phantom_transcript(cleaned, last_assistant=""):
                # is_phantom_transcript without last_assistant only checks hallucination/phantom exact
                # so we keep it
                if cleaned.lower().strip().rstrip(".!?,") in {
                    "thank you", "thanks", "you", "the", "bye", "okay", "ok",
                    "thank you for watching", "thanks for watching",
                }:
                    return True, "phantom"
        except Exception:
            pass

        # 4. Known TTS error lines hard filter
        norm = _normalize(cleaned)
        for err in KNOWN_TTS_ERROR_LINES:
            if _normalize(err) == norm or _is_near_match(norm, _normalize(err)):
                return True, f"tts_error:{err[:20]}"

        # 5. Echo fuzzy match against last spoken
        is_echo, matched = self.is_echo(cleaned)
        if is_echo:
            # double check barge-in — echo wins unless it's clearly a barge phrase
            if self.is_barge_in(cleaned):
                return False, "barge-in-echo-override"
            return True, f"echo:{matched[:30]}"

        return False, "ok"

    # ── VAD helpers ──────────────────────────────────────────────────────────

    def vad_should_arm(self) -> bool:
        """VAD must not arm during TTS or tail."""
        return self.can_listen()

    def wake_should_arm(self) -> bool:
        """Wake-word detector must not arm during TTS or tail."""
        return self.can_listen()

    def get_vad_tuning(self) -> dict:
        """Return tuned VAD thresholds for this Mac (spec §3.5)."""
        import platform
        # MacBook array mics need slightly higher SNR gate
        is_mac = platform.system().lower() == "darwin"
        return {
            "min_speech_frames": VAD_MIN_SPEECH_FRAMES,          # 8 = 240ms
            "min_utterance_frames": VAD_MIN_UTTERANCE_FRAMES,    # 12 = 360ms
            "blip_max_frames": VAD_BLIP_MAX_FRAMES,              # 3 = 90ms
            "frame_ms": 30,
            "silence_frames": 41,  # ~1.25s via config STT_SILENCE_TIMEOUT_S
            "platform": "mac" if is_mac else "other",
            "suppression": self.get_echo_suppression_mode(),
        }

    # ── Test helpers ─────────────────────────────────────────────────────────

    def reset_for_test(self) -> None:
        """Clear spoken history and timing (test only)."""
        with self._lock:
            self._tts_active = False
            self._tts_start_mono = None
            self._tts_end_mono = None
            self._last_spoken.clear()
            self._last_spoken_norm.clear()

    def _set_last_spoken_for_test(self, texts: list[str]) -> None:
        with self._lock:
            self._last_spoken.clear()
            self._last_spoken_norm.clear()
            for t in texts[-LAST_SPOKEN_MAX:]:
                n = _normalize(t)
                if n:
                    self._last_spoken.append(t)
                    self._last_spoken_norm.append(n)

    def _force_tail_for_test(self, remaining_ms: int) -> None:
        """Put controller into tail state with given remaining ms (test only)."""
        with self._lock:
            self._tts_active = False
            # set end time so tail_remaining == remaining_ms
            self._tts_end_mono = time.monotonic() - (self._tail_ms - remaining_ms) / 1000.0


# ── Singleton ────────────────────────────────────────────────────────────────

duplex = DuplexController()

# Convenience module-level aliases (import-friendly)
notify_tts_start = duplex.notify_tts_start
notify_tts_end = duplex.notify_tts_end
can_listen = duplex.can_listen
is_echo = duplex.is_echo
should_drop_transcript = duplex.should_drop_transcript
is_in_tail = duplex.is_in_tail
