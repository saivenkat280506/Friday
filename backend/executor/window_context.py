"""
window_context.py — WindowContextChecker Safety Module
=======================================================
Pre-flight safety check before every type_text, click_at, move_mouse, or hotkey call.
Ensures the focused window and element type match the task intent.
"""

import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

# ── Intent-to-window mapping ────────────────────────────────────────────────────
INTENT_WINDOW_MAP = {
    "send_whatsapp": {
        "window_titles": ["whatsapp"],
        "element_types": ["Edit", "RichEdit", "Document", "TextField"],
    },
    "send_whatsapp_message": {
        "window_titles": ["whatsapp"],
        "element_types": ["Edit", "RichEdit", "Document", "TextField"],
    },
    "search_browser": {
        "window_titles": ["chrome", "edge", "firefox", "brave", "opera", "arc"],
        "element_types": ["Edit", "AddressBar", "ComboBox", "SearchField", "TextField"],
    },
    "search_and_browse": {
        "window_titles": ["chrome", "edge", "firefox", "brave", "opera", "arc"],
        "element_types": ["Edit", "AddressBar", "ComboBox", "SearchField", "TextField"],
    },
    "play_youtube_music": {
        "window_titles": ["chrome", "edge", "firefox", "brave", "opera", "arc"],
        "element_types": ["Edit", "AddressBar", "ComboBox", "SearchField", "TextField"],
    },
    "play_spotify_music": {
        "window_titles": ["chrome", "edge", "firefox", "brave", "opera", "arc", "spotify"],
        "element_types": ["Edit", "SearchField", "TextField"],
    },
    "type_text": {
        "window_titles": [],
        "element_types": [],
        "require_text_input": True,
    },
    "click_at": {
        "window_titles": [],
        "element_types": [],
    },
    "move_mouse": {
        "window_titles": [],
        "element_types": [],
    },
    "hotkey": {
        "window_titles": [],
        "element_types": [],
    },
}

TEXT_INPUT_ELEMENT_TYPES = {
    "Edit", "RichEdit", "Document", "TextField", "ComboBox",
    "SearchField", "AddressBar", "Spinner",
}

TEXT_INPUT_CLASS_HINTS = (
    "edit", "richedit", "scintilla", "textbox", "inputsite",
    "chrome_renderwidgethosthwnd", "internet explorer_server",
    "cascadia", "notepad", "afx:",
)

NON_TEXT_ELEMENT_TYPES = {
    "Button", "CheckBox", "RadioButton", "MenuItem", "MenuBar",
    "ToolBar", "TabItem", "TreeItem", "ListItem", "Hyperlink",
    "ScrollBar", "Thumb", "TitleBar", "SplitButton",
}

APP_ALIASES = {
    "chrome": ["chrome", "google chrome"],
    "edge": ["edge", "microsoft edge"],
    "firefox": ["firefox", "mozilla firefox"],
    "whatsapp": ["whatsapp"],
    "notepad": ["notepad"],
    "spotify": ["spotify"],
    "code": ["visual studio code", "vscode", "code"],
    "word": ["word", "microsoft word"],
    "excel": ["excel", "microsoft excel"],
    "powershell": ["powershell", "windows powershell"],
    "cmd": ["command prompt", "cmd"],
    "terminal": ["terminal", "windows terminal", "wt"],
}


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def get_focus_hwnd() -> int:
    """Return the HWND of the control that currently has keyboard focus."""
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return 0
        tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return info.hwndFocus or info.hwndCaret or hwnd
        return hwnd
    except Exception:
        return 0


def get_active_window_title() -> str:
    """Return the title of the currently focused window."""
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def _normalize_control_type(control_type: str) -> str:
    if not control_type:
        return ""
    return str(control_type).strip()


def _class_suggests_text_input(class_name: str) -> bool:
    if not class_name:
        return False
    lowered = class_name.lower()
    return any(hint in lowered for hint in TEXT_INPUT_CLASS_HINTS)


def _element_is_text_input(control_type: str, class_name: str) -> bool:
    normalized = _normalize_control_type(control_type)
    if normalized in TEXT_INPUT_ELEMENT_TYPES:
        return True
    if _class_suggests_text_input(class_name):
        return True
    return False


def _wrap_focus_element():
    """Return the pywinauto wrapper for the focused control, or None."""
    focus_hwnd = get_focus_hwnd()
    if not focus_hwnd:
        return None
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=focus_hwnd, timeout=2)
        return app.window(handle=focus_hwnd).wrapper_object()
    except Exception:
        return None


def _collect_focus_chain(max_depth: int = 8) -> list[dict]:
    """Walk the UIA parent chain from the focused control."""
    chain = []
    elem = _wrap_focus_element()
    if not elem:
        return chain

    current = elem
    for _ in range(max_depth):
        try:
            info = current.element_info
            chain.append({
                "control_type": _normalize_control_type(info.control_type),
                "class_name": info.class_name or "",
                "name": (info.name or "")[:80],
            })
            parent = current.parent()
            if parent is None:
                break
            current = parent
        except Exception:
            break
    return chain


def get_focus_context() -> dict:
    """
    Inspect the focused control and its ancestors.
    Returns a dict used by pre-flight checks before typing.
    """
    chain = _collect_focus_chain()
    focus_hwnd = get_focus_hwnd()
    current_window = get_active_window_title()

    if not chain:
        class_name = ""
        try:
            import win32gui
            if focus_hwnd:
                class_name = win32gui.GetClassName(focus_hwnd) or ""
        except Exception:
            pass
        is_text_input = _class_suggests_text_input(class_name)
        return {
            "current_window": current_window,
            "focus_hwnd": focus_hwnd,
            "control_type": "",
            "class_name": class_name,
            "name": "",
            "is_text_input": is_text_input,
            "chain": [],
        }

    head = chain[0]
    is_text_input = any(
        _element_is_text_input(item["control_type"], item["class_name"])
        for item in chain
    )

    # Explicit non-input controls should block typing even if a parent looked editable.
    if head["control_type"] in NON_TEXT_ELEMENT_TYPES and not _class_suggests_text_input(head["class_name"]):
        is_text_input = False

    return {
        "current_window": current_window,
        "focus_hwnd": focus_hwnd,
        "control_type": head["control_type"],
        "class_name": head["class_name"],
        "name": head["name"],
        "is_text_input": is_text_input,
        "chain": chain,
    }


def get_focused_element_type() -> str:
    """Return the UIA element type of the currently focused control."""
    return get_focus_context()["control_type"]


def get_focused_text() -> str:
    """Try to extract the label/name of the focused element for debug logging."""
    return get_focus_context()["name"]


def _resolve_target_app(intent: str, params: dict | None = None) -> str:
    """Resolve which app should be in the foreground for this action."""
    params = params or {}
    for key in ("target_app", "app", "app_name"):
        value = params.get(key)
        if value:
            return str(value)

    app_map = {
        "send_whatsapp": "WhatsApp",
        "send_whatsapp_message": "WhatsApp",
        "search_browser": "Chrome",
        "search_and_browse": "Chrome",
        "play_youtube_music": "Chrome",
        "play_spotify_music": "Spotify",
    }
    return app_map.get(intent, "")


def bring_window_to_front(target_title_substring: str) -> bool:
    """Attempt to bring a window matching target_title_substring to the foreground."""
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(target_title_substring)
        if windows:
            win = windows[0]
            win.activate()
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def validate_typing_context(params: dict | None = None, intent: str = "type_text") -> dict:
    """
    Validate that keyboard focus is on a text input in the expected application.
    Returns the same shape as WindowContextChecker.pre_flight().
    """
    checker = WindowContextChecker()
    return checker.pre_flight(intent, params or {})


class WindowContextChecker:
    """
    Pre-flight safety validator for type_text, click_at, move_mouse, hotkey operations.
    """

    BEHAVIOR_AUTO_CORRECT = "auto_correct"
    BEHAVIOR_HOLD_AND_ASK = "hold_and_ask"
    BEHAVIOR_HARD_STOP = "hard_stop"

    def __init__(self):
        self._last_check = {}

    def _get_intent_window_map(self, intent: str) -> dict:
        if intent in INTENT_WINDOW_MAP:
            return INTENT_WINDOW_MAP[intent]
        return INTENT_WINDOW_MAP.get("type_text", {})

    def _merge_expected_windows(self, mapping: dict, params: dict) -> list[str]:
        expected = list(mapping.get("window_titles", []))
        target_app = _resolve_target_app("", params)
        if target_app:
            expected.append(target_app.lower())
            for aliases in APP_ALIASES.values():
                if target_app.lower() in aliases:
                    expected.extend(aliases)
        # De-duplicate while preserving order
        seen = set()
        merged = []
        for item in expected:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                merged.append(key)
        return merged

    def pre_flight(self, intent: str, params: dict) -> dict:
        mapping = self._get_intent_window_map(intent)
        expected_windows = self._merge_expected_windows(mapping, params)
        expected_elements = list(mapping.get("element_types", []))
        require_text_input = mapping.get("require_text_input", bool(expected_elements))

        focus = get_focus_context()
        current_window = focus["current_window"]
        current_element = focus["control_type"]
        current_text = focus["name"]

        self._last_check = {
            "intent": intent,
            "current_window": current_window,
            "current_element": current_element,
            "current_text": current_text,
            "is_text_input": focus["is_text_input"],
            "expected_windows": expected_windows,
            "expected_elements": expected_elements,
            "focus_chain": focus["chain"],
        }

        # Typing intents must land in a real text field — never buttons/menus/etc.
        if require_text_input and not focus["is_text_input"]:
            msg = (
                f"Focus is not on a text input for intent '{intent}': "
                f"window='{current_window[:50]}', element='{current_element or 'unknown'}' "
                f"({focus['class_name'] or 'no class'}). Refusing to type."
            )
            logger.warning(msg)
            return {
                "status": "mismatch",
                "behavior": self.BEHAVIOR_HARD_STOP,
                "message": msg,
                "current_window": current_window,
                "current_element": current_element,
                "expected_windows": expected_windows,
                "expected_elements": expected_elements or list(TEXT_INPUT_ELEMENT_TYPES),
            }

        # Generic pointer/keyboard intents without a target window
        if not expected_windows and not expected_elements:
            return {
                "status": "ok",
                "current_window": current_window,
                "current_element": current_element,
                "is_text_input": focus["is_text_input"],
            }

        current_lower = current_window.lower()
        window_match = not expected_windows or any(exp in current_lower for exp in expected_windows)
        element_match = (
            not expected_elements
            or current_element in expected_elements
            or (require_text_input and focus["is_text_input"])
        )

        if window_match and element_match:
            logger.debug(
                f"WindowContext OK: intent={intent}, window='{current_window[:50]}', "
                f"element={current_element}"
            )
            return {
                "status": "ok",
                "current_window": current_window,
                "current_element": current_element,
                "is_text_input": focus["is_text_input"],
            }

        if not window_match:
            msg = (
                f"Window mismatch for intent '{intent}': "
                f"current='{current_window[:50]}', expected one of {expected_windows}"
            )
            logger.warning(msg)

            target_app = _resolve_target_app(intent, params)
            if target_app and bring_window_to_front(target_app):
                time.sleep(0.5)
                focus = get_focus_context()
                current_window = focus["current_window"]
                current_element = focus["control_type"]
                current_lower = current_window.lower()
                window_match = not expected_windows or any(exp in current_lower for exp in expected_windows)
                element_match = (
                    not expected_elements
                    or current_element in expected_elements
                    or (require_text_input and focus["is_text_input"])
                )
                if window_match and element_match and (not require_text_input or focus["is_text_input"]):
                    return {
                        "status": "ok",
                        "behavior": self.BEHAVIOR_AUTO_CORRECT,
                        "message": f"Auto-corrected: brought '{target_app}' to front",
                        "current_window": current_window,
                        "current_element": current_element,
                        "is_text_input": focus["is_text_input"],
                    }

            return {
                "status": "mismatch",
                "behavior": self.BEHAVIOR_HARD_STOP,
                "message": msg,
                "current_window": current_window,
                "current_element": current_element,
                "expected_windows": expected_windows,
                "expected_elements": expected_elements,
            }

        if not element_match:
            msg = (
                f"Element type mismatch for intent '{intent}': "
                f"current='{current_element}', expected one of {expected_elements}"
            )
            logger.warning(msg)
            return {
                "status": "mismatch",
                "behavior": self.BEHAVIOR_HARD_STOP,
                "message": msg,
                "current_window": current_window,
                "current_element": current_element,
                "expected_windows": expected_windows,
                "expected_elements": expected_elements,
            }

        return {
            "status": "ok",
            "current_window": current_window,
            "current_element": current_element,
            "is_text_input": focus["is_text_input"],
        }

    def get_last_check(self) -> dict:
        return self._last_check