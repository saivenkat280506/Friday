"""
world.py — Phase 2 World Snapshot
==================================

Cheap, always-on desktop perception.

Responsibilities (doc §13 Phase 2):
  1. 1s polling of frontmost app name + active window title (no pixels)
  2. On-demand screen frame capture (JPEG, base64) — only when needed
  3. Same local model for image+text queries
  4. Every tool result followed by a world check (via verify.py)

Design:
  - Pure subprocess/osascript — no extra deps beyond stdlib + Pillow
  - RAM-only: no screen frames stored beyond last capture
  - Max 1 capture per 3s to avoid hammering GPU/RAM on 16GB M1

Usage:
    from perception.world import world_state, get_world_snapshot, capture_screen_frame

    snap = get_world_snapshot()       # instant, cached
    frame_b64 = capture_screen_frame()  # on-demand only
"""

from __future__ import annotations

import asyncio
import base64
import logging
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger("friday.perception.world")

# ── Tunables ──────────────────────────────────────────────────────────────────

POLL_INTERVAL_S: float = 1.0          # how often to refresh app+title
SCREEN_MIN_INTERVAL_S: float = 3.0    # min seconds between screen captures
SCREEN_RETAIN_S: float = 60.0         # Phase 5: clear screen_b64 from RAM after this many seconds
SCREEN_JPEG_QUALITY: int = 60         # balance quality vs. tokens/RAM
SCREEN_MAX_DIMENSION: int = 1280      # downscale to this width/height max

# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class WorldSnapshot:
    """Cheap desktop state. Screen frame is RAM-only and may be None."""
    app: str = ""                        # frontmost app bundle name (short)
    app_display: str = ""               # human-readable name, e.g. "Google Chrome"
    window_title: str = ""              # active window title
    captured_at: float = 0.0           # monotonic time of this snapshot
    screen_b64: str | None = None       # last captured screen frame (on-demand)
    screen_captured_at: float = 0.0    # when the screen frame was captured
    _error: str = ""                    # last poll error (debug)

    def age_s(self) -> float:
        return time.monotonic() - self.captured_at

    def is_stale(self, max_age_s: float = 3.0) -> bool:
        return self.age_s() > max_age_s

    def screen_age_s(self) -> float:
        if self.screen_captured_at == 0.0:
            return float("inf")
        return time.monotonic() - self.screen_captured_at

    def to_context_string(self) -> str:
        """Short string for LLM context injection."""
        parts = []
        if self.app_display:
            parts.append(f"App: {self.app_display}")
        if self.window_title:
            parts.append(f"Window: {self.window_title}")
        return " | ".join(parts) if parts else "Desktop"

    def __repr__(self) -> str:
        return f"<WorldSnapshot app={self.app_display!r} title={self.window_title[:40]!r} age={self.age_s():.1f}s>"


# ── macOS helpers ────────────────────────────────────────────────────────────


def _get_frontmost_app_macos() -> tuple[str, str]:
    """
    Returns (bundle_id_short, display_name) of the frontmost macOS application.
    Uses osascript — fast, no deps, ~5ms.
    """
    script = '''
    tell application "System Events"
        try
            set p to first application process whose frontmost is true
            set pName to name of p
            set pBundle to ""
            try
                set pBundle to bundle identifier of p
            end try
            return (pName as string) & ", " & (pBundle as string)
        on error
            return ""
        end try
    end tell
    '''
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script],
            timeout=3,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        parts = [p.strip() for p in out.split(",", 1)]
        display = parts[0] if parts else ""
        bundle = parts[1] if len(parts) > 1 else ""
        short = bundle.rsplit(".", 1)[-1] if bundle else display
        return short, display
    except Exception as exc:
        logger.debug("[World] frontmost app error: %s", exc)
        return "", ""


def _get_active_window_title_macos() -> str:
    """
    Returns the title of the frontmost window (macOS Accessibility).
    Falls back to an empty string if permissions not granted.
    """
    script = '''
    tell application "System Events"
        try
            set p to first application process whose frontmost is true
            if exists (front window of p) then
                return name of front window of p
            end if
        end try
    end tell
    '''
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script],
            timeout=3,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out and out != "missing value":
            return out
    except Exception:
        pass

    # Fallback: try getting the document name from the active app
    script2 = (
        'tell application (name of first application process whose frontmost is true) '
        'to get name of front document'
    )
    try:
        out2 = subprocess.check_output(
            ["osascript", "-e", script2],
            timeout=2,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out2 and out2 != "missing value":
            return out2
    except Exception:
        pass
    return ""


def _capture_screen_macos(quality: int = SCREEN_JPEG_QUALITY, max_dim: int = SCREEN_MAX_DIMENSION) -> str:
    """
    Capture the primary screen and return as base64 JPEG string.
    RAM-only — caller decides whether to store.
    """
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.check_call(
            ["screencapture", "-x", "-C", tmp_path],
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
        # Resize + convert to JPEG using Pillow
        try:
            from PIL import Image
            import io
            img = Image.open(tmp_path)
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            # Pillow not available — return raw PNG as-is (larger)
            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return b64
    except Exception as exc:
        logger.warning("[World] screen capture failed: %s", exc)
        return ""


# ── Platform dispatch ─────────────────────────────────────────────────────────


def _poll_desktop() -> tuple[str, str, str]:
    """
    Poll the desktop for (app_short, app_display, window_title).
    macOS only for now — returns empty strings on other platforms.
    """
    if platform.system().lower() != "darwin":
        return "", "", ""
    try:
        short, display = _get_frontmost_app_macos()
        title = _get_active_window_title_macos()
        return short, display, title
    except Exception as exc:
        logger.debug("[World] poll_desktop error: %s", exc)
        return "", "", ""


# ── State holder ─────────────────────────────────────────────────────────────


class WorldState:
    """
    Thread-safe holder for the latest world snapshot.
    Updated by the background watcher task.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snap = WorldSnapshot()
        self._running = False
        self._screen_lock = threading.Lock()  # serialise screen captures

    def _update(self, app: str, display: str, title: str) -> None:
        with self._lock:
            self._snap = WorldSnapshot(
                app=app,
                app_display=display,
                window_title=title,
                captured_at=time.monotonic(),
                screen_b64=self._snap.screen_b64,           # keep last frame
                screen_captured_at=self._snap.screen_captured_at,
            )

    def get(self) -> WorldSnapshot:
        with self._lock:
            # Phase 5 — Screenshot retention: clear frame after 60s to free RAM
            snap = self._snap
            if snap.screen_b64 and snap.screen_age_s() > SCREEN_RETAIN_S:
                self._snap = WorldSnapshot(
                    app=snap.app,
                    app_display=snap.app_display,
                    window_title=snap.window_title,
                    captured_at=snap.captured_at,
                    screen_b64=None,           # cleared — RAM freed
                    screen_captured_at=snap.screen_captured_at,
                )
            return self._snap

    def capture_screen(self) -> str:
        """On-demand screen capture. Rate-limited to SCREEN_MIN_INTERVAL_S."""
        with self._lock:
            age = self._snap.screen_age_s()
        if age < SCREEN_MIN_INTERVAL_S:
            # Return cached frame
            with self._lock:
                return self._snap.screen_b64 or ""

        with self._screen_lock:
            # Re-check inside screen lock (another thread may have captured)
            with self._lock:
                age = self._snap.screen_age_s()
            if age < SCREEN_MIN_INTERVAL_S:
                with self._lock:
                    return self._snap.screen_b64 or ""
            b64 = _capture_screen_macos()
            now = time.monotonic()
            with self._lock:
                self._snap.screen_b64 = b64
                self._snap.screen_captured_at = now
            return b64

    async def start_watcher(self) -> None:
        """Background async task: poll desktop every POLL_INTERVAL_S seconds."""
        if self._running:
            return
        self._running = True
        logger.info("[World] Watcher started (poll=%.1fs)", POLL_INTERVAL_S)
        while self._running:
            try:
                app, display, title = await asyncio.to_thread(_poll_desktop)
                self._update(app, display, title)
            except Exception as exc:
                logger.debug("[World] watcher tick error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)

    def stop_watcher(self) -> None:
        self._running = False


# ── Singleton ─────────────────────────────────────────────────────────────────

world_state = WorldState()


def get_world_snapshot() -> WorldSnapshot:
    """Module-level convenience: get current world snapshot (cached)."""
    return world_state.get()


def capture_screen_frame() -> str:
    """Module-level convenience: on-demand screen capture (rate-limited)."""
    return world_state.capture_screen()


async def start_world_watcher() -> None:
    """Start the background world snapshot watcher as an asyncio task."""
    asyncio.create_task(world_state.start_watcher(), name="world-watcher")
    logger.info("[World] World watcher task created")
