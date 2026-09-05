"""
whatsapp_handler.py — Canonical WhatsApp Desktop automation for FRIDAY.

Merges the robust verification flow from automation.py, multi-strategy UI
fallbacks from whatsapp_controller.py, and async integration for LangGraph.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import TypedDict

import pyautogui
try:
    from pywinauto import Application
except ImportError:
    Application = None

from executor.automation import (
    _force_whatsapp_foreground,
    _get_whatsapp_window,
    _hwnd_belongs_to_whatsapp,
    _safe_click,
    _verify_message_sent,
    is_whatsapp_running,
    open_whatsapp,
)
from executor.error_handler import retry_task
from executor.whatsapp_phonebook import match_needles, resolve_contact_keyword
from executor.window_manager import split_screen

logger = logging.getLogger("friday.whatsapp")

# WHATSAPP_DRY_RUN=1 → open/search/focus only, no typing
DRY_RUN = os.getenv("WHATSAPP_DRY_RUN", "").lower() in ("1", "true", "yes")
# WHATSAPP_TYPE_ONLY=1 → type message in compose box, do not press Enter
TYPE_ONLY = os.getenv("WHATSAPP_TYPE_ONLY", "").lower() in ("1", "true", "yes")
FAST_MODE = DRY_RUN or TYPE_ONLY or os.getenv("WHATSAPP_FAST", "").lower() in ("1", "true", "yes")


def _pause(seconds: float) -> None:
    """Sleep with reduced delays when FAST_MODE is active."""
    time.sleep(max(0.05, seconds * 0.25) if FAST_MODE else seconds)


class WhatsAppResult(TypedDict, total=False):
    success: bool
    contact: str
    message: str
    error: str | None
    dry_run: bool
    stage: str
    search_query: str
    from_phonebook: bool


def _foreground_hwnd() -> int:
    try:
        import win32gui

        return win32gui.GetForegroundWindow() or 0
    except Exception:
        return 0


def _active_window_title() -> str:
    try:
        import win32gui

        hwnd = _foreground_hwnd()
        return win32gui.GetWindowText(hwnd) if hwnd else ""
    except Exception:
        return ""


def _is_whatsapp_foreground() -> bool:
    """True only when the foreground window belongs to WhatsApp.exe."""
    return _hwnd_belongs_to_whatsapp(_foreground_hwnd())


def _focus_whatsapp_window() -> bool:
    """Locate WhatsApp Desktop and force active system focus."""
    if not is_whatsapp_running():
        return False

    window = _get_whatsapp_window()
    if not window:
        return False
    try:
        _force_whatsapp_foreground(window)
        _pause(0.35)
        return _is_whatsapp_foreground()
    except Exception as exc:
        logger.warning("WhatsApp focus failed: %s", exc)
        return False


def _ensure_whatsapp_foreground(main_window=None) -> bool:
    """
    Aggressively steal focus to WhatsApp.

    Refuses global keyboard input when Cursor/another app is still foreground —
    that is what caused messages to land in the chat prompt box.
    """
    window = _get_whatsapp_window()
    if window:
        _force_whatsapp_foreground(window)
    _focus_whatsapp_window()
    if main_window:
        try:
            main_window.set_focus()
        except Exception:
            pass

    if _is_whatsapp_foreground():
        return True

    # Alt-key trick helps SetForegroundWindow from automation scripts on Windows
    try:
        pyautogui.press("alt")
        _pause(0.05)
    except Exception:
        pass
    if window:
        _force_whatsapp_foreground(window)
    _focus_whatsapp_window()

    if _is_whatsapp_foreground():
        return True

    logger.error(
        "WhatsApp is not foreground (active=%r) — will not send global keystrokes",
        _active_window_title(),
    )
    return False


def _connect_main_window():
    """Connect to WhatsApp via UIA process handle — never title-only matching."""
    if not is_whatsapp_running():
        return None, None

    window = _get_whatsapp_window()
    if not window:
        return None, None
    try:
        window.set_focus()
        return None, window
    except Exception:
        return None, window


def _click_sidebar_search(main_window) -> bool:
    """
    Focus the left-sidebar global search field.

    Never use Ctrl+F — that opens in-chat find and stays on the current chat.
    """
    try:
        if main_window:
            search_box = main_window.child_window(
                title="Search or start a new chat",
                control_type="Edit",
                found_index=0,
            )
            if search_box.exists():
                search_box.click_input()
                _pause(0.2)
                return True

            edits = main_window.descendants(control_type="Edit")
            if edits:
                edits[0].click_input()
                _pause(0.2)
                return True
    except Exception as exc:
        logger.debug("UIA sidebar search click failed: %s", exc)

    window = _get_whatsapp_window()
    if window:
        try:
            rect = window.rectangle()
            sidebar_x = rect.left + min(220, max(120, rect.width() // 5))
            sidebar_y = rect.top + 55
            if _safe_click(sidebar_x, sidebar_y):
                _pause(0.2)
                return True
        except Exception as exc:
            logger.debug("Coordinate sidebar search click failed: %s", exc)
    return False


def _row_matches_needles(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    digits_only = "".join(c for c in text if c.isdigit())
    for needle in needles:
        n = needle.lower()
        if n in lowered:
            return True
        n_digits = "".join(c for c in needle if c.isdigit())
        if len(n_digits) >= 6 and n_digits in digits_only:
            return True
    return False


def _select_contact_from_results(main_window, needles: list[str]) -> tuple[bool, bool]:
    """
    Pick a contact from sidebar search results.

    Returns (opened, confident_match). confident_match is True when a
    list row explicitly matched a phonebook needle.
    """
    for control_type in ("ListItem", "DataItem", "TreeItem"):
        try:
            if not main_window:
                break
            scanned = 0
            for item in main_window.descendants(control_type=control_type):
                scanned += 1
                if scanned > 40:
                    break
                try:
                    text = item.window_text().strip()
                    if text and _row_matches_needles(text, needles):
                        item.click_input()
                        _pause(0.5 if FAST_MODE else 0.7)
                        logger.info("Opened contact via %s: %s", control_type, text)
                        return True, True
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("%s scan failed: %s", control_type, exc)

    # No blind coordinate fallback — it caused false positives when the wrong
    # window was focused (e.g. IDE tabs titled "WhatsApp ...").
    return False, False


def _read_sidebar_search_text(main_window) -> str:
    """Read the left-sidebar global search field value."""
    if not main_window:
        return ""
    try:
        search_box = main_window.child_window(
            title="Search or start a new chat",
            control_type="Edit",
            found_index=0,
        )
        if search_box.exists():
            return (search_box.window_text() or "").strip()
    except Exception:
        pass
    try:
        edits = main_window.descendants(control_type="Edit")
        if edits:
            return (edits[0].window_text() or "").strip()
    except Exception:
        pass
    return ""


def _sidebar_search_matches(query: str, main_window) -> bool:
    """Confirm the typed query actually landed in the sidebar search box."""
    typed = _read_sidebar_search_text(main_window)
    if not typed:
        return False
    query_digits = "".join(c for c in query if c.isdigit())
    typed_digits = "".join(c for c in typed if c.isdigit())
    if query_digits and len(query_digits) >= 6:
        return query_digits in typed_digits or typed_digits in query_digits
    return query.lower() in typed.lower() or typed.lower() in query.lower()


def _type_in_sidebar_search(query: str, main_window) -> bool:
    """Clear sidebar search and type a query string (WhatsApp must be foreground)."""
    if not _ensure_whatsapp_foreground(main_window):
        return False
    pyautogui.hotkey("ctrl", "a")
    _pause(0.05)
    pyautogui.press("backspace")
    _pause(0.05)
    pyautogui.write(query, interval=0.01 if FAST_MODE else 0.02)
    _pause(0.6 if FAST_MODE else 1.0)
    if not _is_whatsapp_foreground():
        return False
    return _sidebar_search_matches(query, main_window)


def _search_contact(
    keyword: str,
    main_window,
    *,
    pre_queries: list[str] | None = None,
    pre_needles: list[str] | None = None,
) -> tuple[bool, bool, str, bool]:
    """
    Search sidebar and open chat.

    Uses phonebook phone number first, then falls back to keyword name.
    When ``pre_queries`` / ``pre_needles`` are supplied (from the command
    enhancer), they override the local phonebook lookup.

    Returns (opened, confident_match, search_query_used, from_phonebook).
    """
    _focus_whatsapp_window()

    if pre_queries and pre_needles:
        # Enhancer already resolved — use its phone-number queries directly.
        queries = pre_queries
        needles = pre_needles
        from_book = True
        logger.info(
            "Using enhancer-resolved queries for %r: %s",
            keyword,
            queries,
        )
    else:
        display_name, queries, from_book = resolve_contact_keyword(keyword)
        needles = match_needles(keyword)
        logger.info(
            "Resolving %r → queries=%s (phonebook=%s)",
            keyword,
            queries,
            from_book,
        )

    pyautogui.press("escape")
    _pause(0.1)
    pyautogui.press("escape")
    _pause(0.1)

    if not _click_sidebar_search(main_window):
        logger.warning("Could not focus sidebar search field")
        return False, False, "", from_book

    for query in queries:
        logger.info("Sidebar search query: %r", query)
        if not _type_in_sidebar_search(query, main_window):
            continue
        opened, confident = _select_contact_from_results(main_window, needles)
        if opened:
            return True, confident or from_book, query, from_book
        if not _click_sidebar_search(main_window):
            break

    logger.warning("Could not open chat for keyword %r", keyword)
    return False, False, queries[0] if queries else keyword, from_book


def _click_message_box(main_window) -> bool:
    """Focus the message input using UIA strategies with coordinate fallback."""
    if FAST_MODE and main_window:
        try:
            rect = main_window.rectangle()
            if _safe_click(rect.left + (rect.right - rect.left) // 2, rect.bottom - 60):
                _pause(0.2)
                return True
        except Exception:
            pass

    msg_box = None
    if main_window:
        try:
            box = main_window.child_window(
                class_name="x1hx0egp x6ikm8r x1odjw0f x1k6rcq7 x6prxxf",
                control_type="Edit",
                found_index=0,
            )
            if box.exists():
                msg_box = box
        except Exception:
            pass

        if not msg_box:
            try:
                box = main_window.child_window(
                    class_name_re=".*x1hx0egp.*",
                    control_type="Edit",
                    found_index=0,
                )
                if box.exists():
                    msg_box = box
            except Exception:
                pass

        if not msg_box:
            try:
                edits = main_window.descendants(control_type="Edit")
                for edit in reversed(edits):
                    rect = edit.rectangle()
                    if rect.top > 500:
                        msg_box = edit
                        break
                if not msg_box and edits:
                    msg_box = edits[-1]
            except Exception:
                pass

    if msg_box and (not hasattr(msg_box, "exists") or msg_box.exists()):
        msg_box.click_input()
        _pause(0.3)
        return True

    if main_window:
        try:
            rect = main_window.rectangle()
            if _safe_click(rect.left + (rect.right - rect.left) // 2, rect.bottom - 60):
                _pause(0.3)
                return True
        except Exception as exc:
            logger.debug("Bottom-area click failed: %s", exc)
    return False


def _find_compose_edit(main_window):
    """Find the chat compose Edit — right panel, bottom area (not sidebar search)."""
    window = main_window or _get_whatsapp_window()
    if not window:
        return None
    try:
        rect = window.rectangle()
        height = rect.bottom - rect.top
        width = rect.right - rect.left
        best = None
        best_top = -1
        for edit in window.descendants(control_type="Edit"):
            try:
                r = edit.rectangle()
                in_right_panel = r.left > rect.left + width * 0.28
                in_bottom = r.top > rect.top + height * 0.5
                if in_right_panel and in_bottom and r.top > best_top:
                    best_top = r.top
                    best = edit
            except Exception:
                continue
        return best
    except Exception as exc:
        logger.debug("Compose edit scan failed: %s", exc)
    return None


def _focus_compose_box(main_window) -> bool:
    """Focus compose via UIA element click inside WhatsApp window bounds."""
    if not _ensure_whatsapp_foreground(main_window):
        return False

    compose = _find_compose_edit(main_window)
    if compose:
        try:
            compose.click_input()
            _pause(0.25)
            return True
        except Exception as exc:
            logger.debug("Compose UIA click failed: %s", exc)

    window = _get_whatsapp_window() or main_window
    if not window:
        return False
    try:
        rect = window.rectangle()
        cx = rect.left + int((rect.right - rect.left) * 0.62)
        cy = rect.bottom - 55
        if _safe_click(cx, cy):
            _pause(0.3)
            return _is_whatsapp_foreground()
    except Exception as exc:
        logger.debug("Compose coordinate click failed: %s", exc)
    return False


def _type_message_in_compose(message: str, main_window, *, submit: bool) -> bool:
    """
    Type into WhatsApp compose only — never global keys unless WA is foreground.

    Uses UIA element paste first so text cannot leak into Cursor/IDE chat boxes.
    """
    if not _ensure_whatsapp_foreground(main_window):
        return False

    compose = _find_compose_edit(main_window)
    if compose:
        try:
            compose.click_input()
            _pause(0.2)
            compose.type_keys("^a{BACKSPACE}", with_spaces=True)
            _pause(0.1)
            try:
                import pyperclip

                pyperclip.copy(message)
                compose.type_keys("^v", with_spaces=True)
            except Exception:
                compose.type_keys(message, with_spaces=True)
            _pause(0.3)
            if submit:
                compose.type_keys("{ENTER}", with_spaces=True)
            if _compose_contains_text(message, main_window):
                logger.info("Typed message via UIA compose element")
                return True
            logger.debug("UIA compose typing did not update compose text")
        except Exception as exc:
            logger.debug("UIA compose typing failed: %s", exc)

    if not _focus_compose_box(main_window):
        logger.warning("Could not focus compose box")
        return False

    if not _is_whatsapp_foreground():
        logger.error("Lost WhatsApp foreground before paste — aborting")
        return False

    pyautogui.hotkey("ctrl", "a")
    _pause(0.05)
    pyautogui.press("backspace")
    _pause(0.1)
    try:
        import pyperclip

        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        pyautogui.write(message, interval=0.015)
    _pause(0.25)
    if submit:
        pyautogui.press("enter")
    return _is_whatsapp_foreground() and _compose_contains_text(message, main_window)


def _compose_contains_text(message: str, main_window) -> bool:
    """Return True if the compose box contains (part of) the intended message."""
    snippet = (message or "").strip()
    if not snippet:
        return True
    compose = _find_compose_edit(main_window)
    if compose:
        try:
            current = (compose.window_text() or "").strip()
            if snippet[:20].lower() in current.lower():
                return True
        except Exception:
            pass
    return False


def _verify_contact_opened(window, needles: list[str]) -> bool:
    """Confirm the correct chat opened — scan WhatsApp window header only."""
    if not window:
        return False

    attempts = 4 if FAST_MODE else 6
    interval = 0.25 if FAST_MODE else 0.4

    for _ in range(attempts):
        try:
            rect = window.rectangle()
            header_limit = rect.top + 160
            scanned = 0
            for el in window.descendants(control_type="Text"):
                scanned += 1
                if scanned > 40:
                    break
                try:
                    r = el.rectangle()
                    if r.top > header_limit:
                        continue
                    text = el.window_text().strip()
                    if text and _row_matches_needles(text, needles):
                        logger.info("Contact verified in header: %s", text)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        _pause(interval)
    return False


def _whatsapp_already_open() -> bool:
    return is_whatsapp_running() and _get_whatsapp_window() is not None


@retry_task(max_retries=1 if FAST_MODE else 2, delay=1.0 if FAST_MODE else 2.0)
def _execute_send_whatsapp_message(
    contact: str,
    message: str,
    enhanced_params: dict | None = None,
) -> WhatsAppResult:
    contact = (contact or "").strip()
    message = (message or "").strip()
    ep = enhanced_params or {}
    base: WhatsAppResult = {
        "success": False,
        "contact": contact,
        "message": message,
        "error": None,
        "dry_run": DRY_RUN,
        "stage": "init",
    }

    if not contact:
        base["error"] = "Contact name is required."
        return base

    import platform
    if platform.system() == "Darwin":
        import re
        import subprocess
        import urllib.parse
        from executor.whatsapp_phonebook import resolve_contact_keyword

        display_name, search_queries, from_book = resolve_contact_keyword(contact)
        phone = ep.get("phone_number")
        if not phone and search_queries:
            for q in search_queries:
                clean_digits = re.sub(r"\D", "", q)
                if len(clean_digits) >= 10:
                    phone = clean_digits
                    break

        # Fast and direct: If phone number is known, open conversation directly via whatsapp:// URL scheme
        if phone:
            clean_phone = re.sub(r"\D", "", str(phone))
            if message:
                url = f"whatsapp://send?phone={clean_phone}&text={urllib.parse.quote(message)}"
            else:
                url = f"whatsapp://send?phone={clean_phone}"

            subprocess.run(["open", url], check=False)
            time.sleep(0.8)

            if message:
                send_script = '''
                tell application "WhatsApp" to activate
                delay 0.3
                tell application "System Events"
                    tell process "WhatsApp"
                        key code 36
                    end tell
                end tell
                '''
                subprocess.run(["osascript", "-e", send_script], capture_output=True, text=True, timeout=5)

            base["success"] = True
            base["contact"] = display_name or contact
            base["stage"] = "sent" if message else "chat_opened"
            return base

        # Fallback: Search contact in UI via AppleScript
        search_term = display_name if display_name else (search_queries[0] if search_queries else contact)
        escaped_search = search_term.replace('"', '\\"')
        escaped_message = message.replace('"', '\\"') if message else ""

        subprocess.run(["open", "-a", "WhatsApp"], check=False)
        time.sleep(0.5)

        if not message:
            # Search contact, arrow down to select first result, open chat
            script = f'''
            tell application "WhatsApp" to activate
            delay 0.4
            tell application "System Events"
                tell process "WhatsApp"
                    -- Open search
                    keystroke "f" using command down
                    delay 0.2
                    -- Clear search field
                    keystroke "a" using command down
                    key code 51
                    delay 0.1
                    -- Type search number or contact name
                    keystroke "{escaped_search}"
                    delay 0.8
                    -- Highlight first search result
                    key code 125
                    delay 0.2
                    -- Open chat
                    key code 36
                end tell
            end tell
            '''
        else:
            # Search contact, arrow down, open chat, clear compose box, type message, send
            script = f'''
            tell application "WhatsApp" to activate
            delay 0.4
            tell application "System Events"
                tell process "WhatsApp"
                    -- Open search
                    keystroke "f" using command down
                    delay 0.2
                    -- Clear search field
                    keystroke "a" using command down
                    key code 51
                    delay 0.1
                    -- Type search number or contact name
                    keystroke "{escaped_search}"
                    delay 0.8
                    -- Highlight first search result
                    key code 125
                    delay 0.2
                    -- Open chat
                    key code 36
                    delay 0.5
                    -- Focus compose box and clear text field
                    keystroke "a" using command down
                    key code 51
                    delay 0.1
                    -- Type message
                    keystroke "{escaped_message}"
                    delay 0.2
                    -- Send message
                    key code 36
                end tell
            end tell
            '''

        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=12)
            if res.returncode == 0:
                base["success"] = True
                base["contact"] = display_name or contact
                base["stage"] = "sent" if message else "search_complete"
                return base
            else:
                logger.warning("WhatsApp AppleScript returned %d: %s", res.returncode, res.stderr)
                base["error"] = f"WhatsApp automation: {res.stderr.strip()}"
                base["stage"] = "script_error"
                return base
        except Exception as exc:
            base["error"] = str(exc)
            base["stage"] = "script_exception"
            return base

    base["stage"] = "open"
    if _whatsapp_already_open():
        logger.info("WhatsApp already open — skipping launch")
        ok, status = True, "WhatsApp already open."
    else:
        ok, status = open_whatsapp()
    if not ok:
        base["error"] = status
        return base

    window = _get_whatsapp_window()
    if window and not FAST_MODE:
        _force_whatsapp_foreground(window)
    elif window and FAST_MODE:
        _focus_whatsapp_window()

    base["stage"] = "connect"
    _, main_window = _connect_main_window()
    if not main_window and window:
        main_window = window

    needles = match_needles(contact)

    base["stage"] = "search"

    # Use enhancer-resolved queries/needles when available.
    pre_queries = ep.get("search_queries")
    pre_needles = ep.get("match_needles")

    searched, confident_match, search_query, from_book = _search_contact(
        contact,
        main_window,
        pre_queries=pre_queries,
        pre_needles=pre_needles,
    )
    base["search_query"] = search_query
    base["from_phonebook"] = from_book
    if not searched:
        base["error"] = f"Could not search for contact '{contact}'."
        return base

    _pause(0.4 if FAST_MODE else 0.8)
    window = _get_whatsapp_window() or main_window
    if not window:
        base["error"] = "Could not find WhatsApp window after search."
        return base

    base["stage"] = "verify_contact"
    verified = _verify_contact_opened(window, needles)
    if not verified:
        base["error"] = (
            f"Contact '{contact}' not found in chat header after search. "
            "Aborted to avoid typing into the wrong chat."
        )
        return base

    if not message:
        base["success"] = True
        base["stage"] = "search_only"
        return base

    if DRY_RUN:
        logger.info("DRY_RUN active — skipping compose for %s", contact)
        base["success"] = True
        base["stage"] = "dry_run_complete"
        return base

    submit = not TYPE_ONLY
    base["stage"] = "type_only" if TYPE_ONLY else "send"
    if not _type_message_in_compose(message, main_window, submit=submit):
        base["error"] = "Could not type message in the WhatsApp compose box."
        return base

    if TYPE_ONLY:
        logger.info("TYPE_ONLY — message typed in compose box, Enter not pressed")
        base["success"] = True
        base["stage"] = "type_only_complete"
        return base

    _pause(0.6 if FAST_MODE else 1.2)
    snippet = message[:60].strip()
    bubble_ok = _verify_message_sent(window, message) or (
        len(snippet) >= 12 and _verify_message_sent(window, snippet)
    )
    if not bubble_ok:
        logger.warning(
            "Bubble verify inconclusive for %s — message was typed and Enter pressed",
            contact,
        )
        base["success"] = True
        base["stage"] = "complete_unverified"
        base["error"] = None
        return base

    try:
        _pause(0.5 if FAST_MODE else 1.0)
        split_screen("FRIDAY", "WhatsApp", left_ratio=0.4)
    except Exception as exc:
        logger.warning("Split screen arrangement failed: %s", exc)

    base["success"] = True
    base["stage"] = "complete"
    return base


async def send_whatsapp_message(
    contact: str,
    message: str,
    enhanced_params: dict | None = None,
) -> WhatsAppResult:
    """Async entry point for LangGraph and tool_executor."""
    return await asyncio.to_thread(
        _execute_send_whatsapp_message, contact, message, enhanced_params
    )


def send_whatsapp_message_sync(
    contact: str,
    message: str,
    enhanced_params: dict | None = None,
) -> WhatsAppResult:
    """Sync entry point for legacy sync handler registry."""
    return _execute_send_whatsapp_message(contact, message, enhanced_params)