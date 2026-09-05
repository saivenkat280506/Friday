"""
presence.py — Phase 1 Presence Modes
=====================================

Controls *whether* Friday is present and how much she speaks.

Three modes (doc §13 Phase 1):
    RESIDENT  — always-on companion, continuous voice, normal initiative
    QUIET     — overlay visible, wake-word only, no auto-listen, reduced initiative
    SLEEP     — mic off, overlay collapsed, no initiative, hotkey-only wake

State is process-wide and thread-safe. The voice_loop, inner_loop, and
companion_state all read this before arming the mic or speaking.

Usage:
    from services.presence import presence, PresenceMode
    if presence.is_sleeping():
        return  # do not speak
    await presence.set_mode(PresenceMode.SLEEP, reason="user request")
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from enum import Enum
from typing import Callable

logger = logging.getLogger("friday.presence")

# ── Enum ─────────────────────────────────────────────────────────────────────


class PresenceMode(str, Enum):
    RESIDENT = "resident"   # full companion — always-on voice + initiative
    QUIET    = "quiet"      # wake-word only, reduced chatter
    SLEEP    = "sleep"      # mic off, overlay collapsed, silent


# ── Patterns that map user speech → presence intent ──────────────────────────

_SLEEP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(go to sleep|go quiet|sleep mode|be quiet|shut up for|mute yourself|give me (?:a |an )?\w+ (hour|minute|min)s?|leave me alone|stop listening|go away)\b", re.I),
    re.compile(r"\bgive me (?:an? )?\d+ (hour|minute|min)s?\b", re.I),
    re.compile(r"\bsleep for (?:an? )?\d+", re.I),
]

_QUIET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(quiet mode|go quiet|just watch|watch mode|be less chatty|stop talking unless|only speak when)\b", re.I),
]

_WAKE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(come back|wake up|i('m| am) back|resume|start listening again|turn on(?: voice)?|hey friday|good (morning|afternoon|evening))\b", re.I),
    re.compile(r"\bresident mode\b", re.I),
]

_DURATION_RE = re.compile(r"(\d+|an?)\s*(hour|hr|minute|min|second|sec)s?", re.I)

_DURATION_MULTIPLIERS = {
    "hour": 3600, "hr": 3600,
    "minute": 60, "min": 60,
    "second": 1, "sec": 1,
}


def _parse_duration_s(text: str) -> float | None:
    """Extract seconds from '30 minutes', '1 hour', 'an hour', 'a minute', etc. Returns None if not found."""
    m = _DURATION_RE.search(text)
    if m:
        raw_val = m.group(1).lower()
        val = 1 if raw_val in ("a", "an") else int(raw_val)
        unit = m.group(2).lower().rstrip("s")
        mult = _DURATION_MULTIPLIERS.get(unit, 60)
        return float(val * mult)
    return None


# ── Intent classifier (no LLM — rule-based only) ─────────────────────────────


def classify_presence_intent(text: str) -> tuple[PresenceMode | None, float | None]:
    """
    Returns (new_mode, duration_seconds) if text is a presence command, else (None, None).
    duration_seconds is only set for timed sleep ("give me 30 minutes").
    """
    for pat in _SLEEP_PATTERNS:
        if pat.search(text):
            dur = _parse_duration_s(text)
            return PresenceMode.SLEEP, dur
    for pat in _QUIET_PATTERNS:
        if pat.search(text):
            return PresenceMode.QUIET, None
    for pat in _WAKE_PATTERNS:
        if pat.search(text):
            return PresenceMode.RESIDENT, None
    return None, None


# ── Presence state ────────────────────────────────────────────────────────────


class PresenceState:
    """
    Thread-safe presence mode holder.

    voice_loop   → calls can_arm_mic() before opening mic
    inner_loop   → calls can_speak_unsolicited() before initiative
    voice_loop   → calls is_sleeping() to skip listen cycle
    companion    → subscribes via add_listener() to update overlay
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mode: PresenceMode = PresenceMode.RESIDENT
        self._mode_since: float = time.monotonic()
        self._sleep_until: float | None = None
        self._listeners: list[Callable[[PresenceMode], None]] = []
        self._wakeup_task: asyncio.Task | None = None

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_mode(self) -> PresenceMode:
        with self._lock:
            # Auto-expire timed sleep
            if self._mode == PresenceMode.SLEEP and self._sleep_until is not None:
                if time.monotonic() >= self._sleep_until:
                    self._mode = PresenceMode.RESIDENT
                    self._sleep_until = None
                    logger.info("[Presence] Timed sleep expired → RESIDENT")
                    self._notify_listeners(PresenceMode.RESIDENT)
            return self._mode

    def is_sleeping(self) -> bool:
        return self.get_mode() == PresenceMode.SLEEP

    def is_quiet(self) -> bool:
        return self.get_mode() == PresenceMode.QUIET

    def is_resident(self) -> bool:
        return self.get_mode() == PresenceMode.RESIDENT

    def can_arm_mic(self) -> bool:
        """Voice loop gate: False in SLEEP mode (mic must not open)."""
        mode = self.get_mode()
        return mode != PresenceMode.SLEEP

    def can_listen_continuous(self) -> bool:
        """Continuous voice mode gate: only in RESIDENT."""
        return self.get_mode() == PresenceMode.RESIDENT

    def can_speak_unsolicited(self) -> bool:
        """Inner loop initiative gate: only in RESIDENT."""
        return self.get_mode() == PresenceMode.RESIDENT

    def sleep_remaining_s(self) -> float:
        with self._lock:
            if self._sleep_until is None or self._mode != PresenceMode.SLEEP:
                return 0.0
            remaining = self._sleep_until - time.monotonic()
            return max(0.0, remaining)

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def set_mode(
        self,
        mode: PresenceMode,
        *,
        reason: str = "",
        duration_s: float | None = None,
    ) -> None:
        """
        Change presence mode. Call from async context (voice_loop, command processor).
        duration_s: if set and mode is SLEEP, auto-wake after this many seconds.
        """
        with self._lock:
            old = self._mode
            self._mode = mode
            self._mode_since = time.monotonic()
            if mode == PresenceMode.SLEEP and duration_s is not None:
                self._sleep_until = time.monotonic() + duration_s
            else:
                self._sleep_until = None

        label = mode.value.upper()
        dur_str = f" for {duration_s:.0f}s" if duration_s else ""
        logger.info("[Presence] %s → %s%s (reason=%s)", old.value.upper(), label, dur_str, reason or "—")

        # Apply side-effects
        await self._apply_mode(mode, old_mode=old, duration_s=duration_s)
        self._notify_listeners(mode)

    def set_mode_sync(self, mode: PresenceMode, *, reason: str = "", duration_s: float | None = None) -> None:
        """Synchronous variant for use from non-async contexts (hotkey callbacks, tests)."""
        with self._lock:
            self._mode = mode
            self._mode_since = time.monotonic()
            if mode == PresenceMode.SLEEP and duration_s is not None:
                self._sleep_until = time.monotonic() + duration_s
            else:
                self._sleep_until = None
        logger.info("[Presence] sync → %s (reason=%s)", mode.value.upper(), reason or "—")
        self._notify_listeners(mode)

    # ── Side-effects ─────────────────────────────────────────────────────────

    async def _apply_mode(
        self,
        mode: PresenceMode,
        *,
        old_mode: PresenceMode,
        duration_s: float | None,
    ) -> None:
        from services.runtime_state import flags, SystemState, set_state

        if mode == PresenceMode.SLEEP:
            # Hard-stop mic and continuous voice
            flags.continuous_voice_mode = False
            flags.stop_listen_trigger = True
            flags.force_listen_trigger = False
            flags.pending_ui_listen = False
            try:
                from services.voice_loop import cancel_active_listen
                cancel_active_listen()
            except Exception as exc:
                logger.debug("[Presence] cancel_active_listen: %s", exc)
            try:
                from tts.pocket_tts import stop_speech
                stop_speech()
            except Exception:
                pass
            await set_state(SystemState.IDLE)
            # Collapse the companion overlay
            try:
                from services.companion_state import _set_task, CompanionTask, broadcast_companion_task
                flags.companion_surface_collapsed = True
                _set_task(CompanionTask(
                    kind="sleep",
                    title="Sleeping",
                    detail="Say 'Hey Friday, I'm back' to wake",
                ))
                await broadcast_companion_task()
            except Exception as exc:
                logger.debug("[Presence] companion collapse: %s", exc)
            # Schedule auto-wake if timed
            if duration_s is not None:
                self._schedule_auto_wake(duration_s)

        elif mode == PresenceMode.QUIET:
            # Stop continuous listen; keep wake-word running
            flags.continuous_voice_mode = False
            flags.stop_listen_trigger = True
            try:
                from services.voice_loop import cancel_active_listen
                cancel_active_listen(keep_continuous_mode=True)
            except Exception:
                pass
            await set_state(SystemState.IDLE)
            try:
                from services.companion_state import set_working_task
                await set_working_task("Watching", "Wake-word only…", kind="quiet")
            except Exception as exc:
                logger.debug("[Presence] companion quiet: %s", exc)

        elif mode == PresenceMode.RESIDENT:
            # Resume normal continuous voice
            flags.companion_surface_collapsed = False
            flags.continuous_voice_mode = True
            flags.stop_listen_trigger = False
            flags.stt_consecutive_failures = 0
            flags.stt_mic_paused_until = 0.0
            try:
                from services.runtime_state import stop_event
                stop_event.clear()
            except Exception:
                pass
            try:
                from services.companion_state import start_companion_listening
                await start_companion_listening()
            except Exception as exc:
                logger.debug("[Presence] start_companion_listening: %s", exc)

    def _schedule_auto_wake(self, duration_s: float) -> None:
        """Schedule a coroutine to auto-wake after timed sleep."""
        async def _wake_task() -> None:
            await asyncio.sleep(duration_s)
            if self.is_sleeping() and self.sleep_remaining_s() <= 0.5:
                logger.info("[Presence] Auto-wake after %.0fs sleep", duration_s)
                await self.set_mode(PresenceMode.RESIDENT, reason="timed_sleep_expired")
                try:
                    from tts.hybrid_tts import speak_hybrid
                    await speak_hybrid("I'm back.", is_smart=False, response_id="presence_wake")
                except Exception:
                    pass
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                t = loop.create_task(_wake_task())
                with self._lock:
                    self._wakeup_task = t
        except RuntimeError:
            pass  # No event loop — sync context, timed expiry handled in get_mode()

    # ── Listeners ─────────────────────────────────────────────────────────────

    def add_listener(self, cb: Callable[[PresenceMode], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def _notify_listeners(self, mode: PresenceMode) -> None:
        for cb in list(self._listeners):
            try:
                cb(mode)
            except Exception as exc:
                logger.debug("[Presence] listener error: %s", exc)

    # ── Runtime_state sync ────────────────────────────────────────────────────

    def sync_to_runtime_flags(self) -> None:
        """Write current mode into RuntimeFlags.presence_mode (string)."""
        try:
            from services.runtime_state import flags
            flags.presence_mode = self._mode.value  # type: ignore[attr-defined]
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────

presence = PresenceState()
