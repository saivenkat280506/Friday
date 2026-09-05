"""
attention.py — Phase 3 Attention Policy
=========================================

Decides *whether* Friday should speak unsolicited in a given inner-loop tick.

Doc §13 Phase 3 rules (presence rules):
  - If the user is in flow (rapid typing, same editor, no question) → quiet
  - If she already said it 30 seconds ago → do not say it again
  - If she is unsure → one question, not a paragraph
  - If she made a mistake → short admission, then the fix
  - Humor only when the turn is social, never while deleting files

Rate limit: max 1 unsolicited spoken line per RATE_LIMIT_MINUTES unless urgent=True.

Usage:
    from brain.attention import attention_policy
    if attention_policy.should_speak(urgent=False):
        # Friday may speak
        attention_policy.record_spoke()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger("friday.attention")

# ── Tunables ──────────────────────────────────────────────────────────────────

RATE_LIMIT_MINUTES: float = 5.0          # max 1 unsolicited line per N minutes
RATE_LIMIT_URGENT_S: float = 30.0        # urgent messages: min 30s gap
REPEAT_SUPPRESS_S: float = 30.0          # don't repeat same content within 30s
FLOW_STATE_GRACE_S: float = 10.0         # don't interrupt if user typed recently
RECENT_HISTORY_MAX: int = 20             # how many recent speaks to track


# ── Context passed to should_speak ───────────────────────────────────────────


class SpeakContext:
    """Snapshot of system state for the attention gate."""

    def __init__(
        self,
        *,
        urgent: bool = False,
        world_app: str = "",
        world_title: str = "",
        last_typing_at: float = 0.0,      # monotonic time of last key event (if tracked)
        content: str = "",                 # what Friday intends to say (for dedup)
    ) -> None:
        self.urgent = urgent
        self.world_app = world_app
        self.world_title = world_title
        self.last_typing_at = last_typing_at
        self.content = content
        self.now = time.monotonic()


# ── Policy ────────────────────────────────────────────────────────────────────


class AttentionPolicy:
    """
    Thread-safe attention gate.

    All inner-loop initiative goes through should_speak().
    record_spoke() must be called after every successful unsolicited speech.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_spoke_at: float = 0.0       # monotonic
        self._recent_contents: deque[tuple[float, str]] = deque(maxlen=RECENT_HISTORY_MAX)

    def _in_flow_state(self, ctx: SpeakContext) -> bool:
        """User is in flow: typed recently in same editor app."""
        if ctx.last_typing_at <= 0:
            return False
        elapsed = ctx.now - ctx.last_typing_at
        return elapsed < FLOW_STATE_GRACE_S

    def _rate_limited(self, urgent: bool) -> bool:
        with self._lock:
            elapsed = time.monotonic() - self._last_spoke_at
        if urgent:
            return elapsed < RATE_LIMIT_URGENT_S
        return elapsed < RATE_LIMIT_MINUTES * 60

    def _is_repeat(self, content: str) -> bool:
        """Return True if this content was spoken recently (within REPEAT_SUPPRESS_S)."""
        if not content:
            return False
        norm = content.lower().strip()[:100]
        now = time.monotonic()
        with self._lock:
            for t, c in self._recent_contents:
                if now - t < REPEAT_SUPPRESS_S and norm in c.lower():
                    return True
        return False

    def _listening_or_processing(self) -> bool:
        """Don't interrupt while mic is open or Friday is working."""
        try:
            from services.runtime_state import flags
            return flags.is_listening or flags.is_processing
        except Exception:
            return False

    def _tts_active(self) -> bool:
        try:
            from tts.pocket_tts import is_tts_active
            return bool(is_tts_active())
        except Exception:
            return False

    def _presence_allows(self) -> bool:
        """Presence mode gate: only RESIDENT allows unsolicited speech."""
        try:
            from services.presence import presence
            return presence.can_speak_unsolicited()
        except Exception:
            return True  # fail open if presence module not available

    def should_speak(self, ctx: SpeakContext | None = None, *, urgent: bool = False) -> bool:
        """
        Main gate. Returns True if Friday is allowed to speak unsolicited.

        Override with urgent=True for time-sensitive alerts (build failed,
        meeting in 1 minute). Even urgent speech respects RATE_LIMIT_URGENT_S.
        """
        if ctx is None:
            ctx = SpeakContext(urgent=urgent)

        # 1. Presence mode gate (SLEEP/QUIET → no initiative)
        if not self._presence_allows():
            logger.debug("[Attention] blocked: presence mode")
            return False

        # 2. Don't interrupt active listening or processing
        if self._listening_or_processing():
            logger.debug("[Attention] blocked: listening/processing")
            return False

        # 3. Don't pile on top of TTS already playing
        if self._tts_active():
            logger.debug("[Attention] blocked: TTS active")
            return False

        # 4. Flow state (user is typing) — bypassed by urgent
        if not (ctx.urgent or urgent) and self._in_flow_state(ctx):
            logger.debug("[Attention] blocked: user in flow")
            return False

        # 5. Rate limit
        if self._rate_limited(ctx.urgent or urgent):
            logger.debug("[Attention] blocked: rate limited")
            return False

        # 6. Repeat suppression
        if ctx.content and self._is_repeat(ctx.content):
            logger.debug("[Attention] blocked: repeat content")
            return False

        return True

    def record_spoke(self, content: str = "") -> None:
        """Call after every successful unsolicited speech act."""
        now = time.monotonic()
        with self._lock:
            self._last_spoke_at = now
            if content:
                norm = content.lower().strip()[:100]
                self._recent_contents.append((now, norm))

    def time_until_allowed_s(self, urgent: bool = False) -> float:
        """How many seconds until the next unsolicited speak is permitted."""
        with self._lock:
            elapsed = time.monotonic() - self._last_spoke_at
        if urgent:
            remain = RATE_LIMIT_URGENT_S - elapsed
        else:
            remain = RATE_LIMIT_MINUTES * 60 - elapsed
        return max(0.0, remain)

    def reset_for_test(self) -> None:
        with self._lock:
            self._last_spoke_at = 0.0
            self._recent_contents.clear()


# ── Singleton ─────────────────────────────────────────────────────────────────

attention_policy = AttentionPolicy()
