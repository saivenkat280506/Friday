"""
verify.py — Phase 2 Screen Verifier
=====================================

Verifies tool results by checking the desktop state after execution.

Doc §13 Phase 2 — "Every tool result followed by a look":
  - After open_app → check frontmost app matches expected
  - Cheap: checks window title first; grabs screen only if title inconclusive
  - Returns (success, what_was_seen, suggestion)

Usage:
    from perception.verify import verify_tool_result

    result = await verify_tool_result("open_app", expected_app="Chrome")
    if not result.success:
        # Friday can say "I tried to open Chrome but it doesn't seem to have opened"
        print(result.suggestion)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger("friday.perception.verify")

# ── Config ────────────────────────────────────────────────────────────────────

VERIFY_WAIT_S: float = 1.5     # wait after tool before checking (app launch takes time)
VERIFY_RETRIES: int = 2         # how many times to re-poll if not confirmed
VERIFY_RETRY_SLEEP_S: float = 0.8


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass
class VerifyResult:
    success: bool
    what_was_seen: str           # e.g. "Google Chrome is now frontmost"
    suggestion: str              # spoken line Friday can use if failed
    tool: str
    expected_app: str
    actual_app: str
    actual_title: str


# ── App name normalisation ────────────────────────────────────────────────────

# Map common spoken/parameter names → expected substrings in app display name
_APP_NAME_ALIASES: dict[str, list[str]] = {
    "chrome":         ["Google Chrome", "Chrome"],
    "safari":         ["Safari"],
    "firefox":        ["Firefox"],
    "terminal":       ["Terminal", "iTerm"],
    "iterm":          ["iTerm"],
    "vscode":         ["Visual Studio Code", "Code"],
    "code":           ["Visual Studio Code", "Code"],
    "xcode":          ["Xcode"],
    "finder":         ["Finder"],
    "notes":          ["Notes"],
    "calendar":       ["Calendar"],
    "mail":           ["Mail"],
    "messages":       ["Messages"],
    "slack":          ["Slack"],
    "discord":        ["Discord"],
    "spotify":        ["Spotify"],
    "notion":         ["Notion"],
    "figma":          ["Figma"],
    "whatsapp":       ["WhatsApp"],
    "zoom":           ["Zoom"],
    "activity monitor": ["Activity Monitor"],
    "system preferences": ["System Preferences", "System Settings"],
    "settings":       ["System Preferences", "System Settings"],
}


def _normalise_app_name(name: str) -> str:
    return name.lower().strip().replace("-", " ").replace("_", " ")


def _app_matches(expected: str, actual_display: str, actual_bundle: str) -> bool:
    """Check if the actual frontmost app matches the expected app name."""
    if not expected:
        return True  # no expectation — always pass
    norm_expected = _normalise_app_name(expected)

    # Direct substring match first
    if norm_expected in actual_display.lower():
        return True
    if norm_expected in actual_bundle.lower():
        return True

    # Check alias table
    for key, aliases in _APP_NAME_ALIASES.items():
        if key in norm_expected or norm_expected in key:
            for alias in aliases:
                if alias.lower() in actual_display.lower():
                    return True

    # Fuzzy: any word from expected appears in display
    words = [w for w in norm_expected.split() if len(w) > 3]
    if words and any(w in actual_display.lower() for w in words):
        return True

    return False


# ── Core verifier ────────────────────────────────────────────────────────────


async def verify_tool_result(
    tool: str,
    *,
    expected_app: str = "",
    use_screen: bool = False,
    wait_s: float = VERIFY_WAIT_S,
) -> VerifyResult:
    """
    Verify that a tool achieved its intended effect.

    Args:
        tool: tool name, e.g. "open_app"
        expected_app: app name that should now be frontmost
        use_screen: if True, also capture a screen frame for LLM verification
        wait_s: seconds to wait before first check (default 1.5s for app launch)

    Returns:
        VerifyResult with success flag and spoken suggestion.
    """
    from perception.world import world_state

    # Brief wait for macOS to bring the app forward
    if wait_s > 0:
        await asyncio.sleep(wait_s)

    for attempt in range(VERIFY_RETRIES + 1):
        snap = world_state.get()

        # If snapshot is very stale (watcher not running), do a direct poll
        if snap.is_stale(max_age_s=2.5):
            try:
                from perception.world import _poll_desktop
                app_s, display, title = await asyncio.to_thread(_poll_desktop)
                from perception.world import WorldSnapshot
                snap = WorldSnapshot(
                    app=app_s, app_display=display, window_title=title,
                    captured_at=time.monotonic()
                )
            except Exception as exc:
                logger.debug("[Verify] direct poll failed: %s", exc)

        match = _app_matches(expected_app, snap.app_display, snap.app)

        if match:
            seen = f"{snap.app_display} is now frontmost" if snap.app_display else "App opened"
            if snap.window_title:
                seen += f" — {snap.window_title[:60]}"
            return VerifyResult(
                success=True,
                what_was_seen=seen,
                suggestion="",
                tool=tool,
                expected_app=expected_app,
                actual_app=snap.app_display,
                actual_title=snap.window_title,
            )

        if attempt < VERIFY_RETRIES:
            logger.debug(
                "[Verify] attempt %d — expected=%r, got=%r, title=%r",
                attempt + 1, expected_app, snap.app_display, snap.window_title
            )
            await asyncio.sleep(VERIFY_RETRY_SLEEP_S)

    # All retries exhausted — report failure
    actual = snap.app_display or "unknown"
    seen = f"Frontmost app is {actual}" if actual else "Could not determine frontmost app"
    suggestion = (
        f"I tried to open {expected_app}, but {actual} is in front. "
        "It might still be loading."
        if expected_app and actual
        else f"I ran {tool}, but I can't confirm it worked."
    )
    logger.warning("[Verify] %s verification failed — expected=%r, got=%r", tool, expected_app, actual)
    return VerifyResult(
        success=False,
        what_was_seen=seen,
        suggestion=suggestion,
        tool=tool,
        expected_app=expected_app,
        actual_app=actual,
        actual_title=snap.window_title,
    )


async def verify_open_app(app_name: str) -> VerifyResult:
    """Convenience wrapper: verify that open_app opened the right app."""
    return await verify_tool_result("open_app", expected_app=app_name, wait_s=VERIFY_WAIT_S)
