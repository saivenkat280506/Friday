"""
win32_hotkey.py — Safe Windows RegisterHotKey listener (no low-level keyboard hooks).

Avoids the ``keyboard`` package low-level hook + suppress path that can crash
Python with access violations on Alt+Space and other system combos.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Optional

logger = logging.getLogger("friday.win32_hotkey")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LRESULT = ctypes.c_int64
    WPARAM = ctypes.c_uint64
    LPARAM = ctypes.c_int64
else:
    LRESULT = ctypes.c_long
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312
WM_DESTROY = 0x0002
WM_QUIT = 0x0012

_next_hotkey_id = 0x4652  # "FR"
_hotkey_id_lock = threading.Lock()

_VK_BY_KEY = {
    "space": 0x20,
    "`": 0xC0,
    "backtick": 0xC0,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}
for i in range(10):
    _VK_BY_KEY[str(i)] = 0x30 + i


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WndProcType = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


def _parse_accelerator(label: str) -> tuple[int, int]:
    """Parse Electron-style accelerator into (modifiers, vk)."""
    parts = [p.strip().lower() for p in label.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")

    mods = 0
    key = parts[-1]
    for part in parts[:-1]:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "super", "meta"):
            mods |= MOD_WIN
        else:
            raise ValueError(f"unknown modifier: {part}")

    if len(key) == 1 and key.isalpha():
        vk = ord(key.upper())
    elif key in _VK_BY_KEY:
        vk = _VK_BY_KEY[key]
    else:
        raise ValueError(f"unknown key: {key}")

    return mods, vk


def _allocate_hotkey_id() -> int:
    global _next_hotkey_id
    with _hotkey_id_lock:
        hotkey_id = _next_hotkey_id
        _next_hotkey_id += 1
        return hotkey_id


class Win32HotkeyListener:
    """Register a global hotkey and invoke a callback on a daemon thread."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._registered = False
        self._hwnd: Optional[int] = None
        self._hotkey_id: int = 0
        self._callback: Optional[Callable[[], None]] = None
        self._wnd_proc_ref: Optional[WndProcType] = None

    @property
    def active(self) -> bool:
        return self._registered

    def start(self, label: str, callback: Callable[[], None]) -> bool:
        if self._thread and self._thread.is_alive():
            self.stop()

        self._callback = callback
        self._stop.clear()
        self._ready.clear()
        self._registered = False

        try:
            mods, vk = _parse_accelerator(label)
        except ValueError as exc:
            logger.warning("Win32 hotkey parse failed for %r: %s", label, exc)
            return False

        self._hotkey_id = _allocate_hotkey_id()
        self._thread = threading.Thread(
            target=self._message_loop,
            args=(mods, vk, label, self._hotkey_id),
            name=f"Win32Hotkey-{label}",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self._registered

    def stop(self) -> None:
        self._stop.set()
        hwnd = self._hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_DESTROY, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._hwnd = None
        self._registered = False

    def _message_loop(self, mods: int, vk: int, label: str, hotkey_id: int) -> None:
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY and wparam == hotkey_id:
                try:
                    if self._callback:
                        self._callback()
                except Exception as exc:
                    logger.warning("Win32 hotkey callback error: %s", exc)
                return LRESULT(0).value
            if msg == WM_DESTROY:
                user32.UnregisterHotKey(hwnd, hotkey_id)
                user32.PostQuitMessage(0)
                return LRESULT(0).value
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WndProcType(wnd_proc)
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"FridayCompanionHotkey{hotkey_id}"

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p)
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            logger.warning("RegisterClassW failed for Win32 hotkey")
            self._ready.set()
            return

        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "FRIDAY Hotkey",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            hinstance,
            None,
        )
        if not hwnd:
            logger.warning("CreateWindowExW failed for Win32 hotkey")
            self._ready.set()
            return

        self._hwnd = hwnd
        if not user32.RegisterHotKey(hwnd, hotkey_id, mods, vk):
            err = kernel32.GetLastError()
            logger.warning(
                "%s Win32 RegisterHotKey failed (error=%s) — combo may be taken",
                label,
                err,
            )
            user32.DestroyWindow(hwnd)
            self._hwnd = None
            self._ready.set()
            return

        self._registered = True
        logger.info("%s registered via Win32 RegisterHotKey", label)
        self._ready.set()

        msg = MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), hwnd, 0, 0)
            if result == 0 or result == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.UnregisterHotKey(hwnd, hotkey_id)
        except Exception:
            pass
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        self._hwnd = None
        self._registered = False


VK_MENU = 0x12
VK_SPACE = 0x20
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
HC_ACTION = 0

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


LowLevelProcType = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class LowLevelHotkeyListener:
    """Low-level keyboard hook when RegisterHotKey cannot claim Alt+Space."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._registered = False
        self._hook: Optional[int] = None
        self._callback: Optional[Callable[[], None]] = None
        self._proc_ref: Optional[LowLevelProcType] = None
        self._mods = 0
        self._vk = 0
        self._label = ""

    @property
    def active(self) -> bool:
        return self._registered

    def start(self, label: str, callback: Callable[[], None]) -> bool:
        if self._thread and self._thread.is_alive():
            self.stop()

        try:
            mods, vk = _parse_accelerator(label)
        except ValueError as exc:
            logger.warning("Low-level hotkey parse failed for %r: %s", label, exc)
            return False

        self._callback = callback
        self._mods = mods
        self._vk = vk
        self._label = label
        self._stop.clear()
        self._ready.clear()
        self._registered = False

        self._thread = threading.Thread(
            target=self._hook_loop,
            name=f"LowLevelHotkey-{label}",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self._registered

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._hook = None
        self._registered = False

    def _mods_pressed(self) -> bool:
        if self._mods & MOD_ALT:
            if not (user32.GetAsyncKeyState(VK_MENU) & 0x8000):
                return False
        if self._mods & MOD_CONTROL:
            if not (user32.GetAsyncKeyState(0x11) & 0x8000):
                return False
        if self._mods & MOD_SHIFT:
            if not (user32.GetAsyncKeyState(0x10) & 0x8000):
                return False
        if self._mods & MOD_WIN:
            if not (
                (user32.GetAsyncKeyState(0x5B) & 0x8000)
                or (user32.GetAsyncKeyState(0x5C) & 0x8000)
            ):
                return False
        return True

    def _hook_loop(self) -> None:
        hook_handle: list[int] = []

        def low_level_proc(n_code, w_param, l_param):
            if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == self._vk and self._mods_pressed():
                    try:
                        if self._callback:
                            self._callback()
                    except Exception as exc:
                        logger.warning("Low-level hotkey callback error: %s", exc)
                    # Swallow the combo so Windows does not open the system menu.
                    return LRESULT(1).value
            hook = hook_handle[0] if hook_handle else None
            if hook:
                return user32.CallNextHookEx(hook, n_code, w_param, l_param)
            return LRESULT(0).value

        self._proc_ref = LowLevelProcType(low_level_proc)
        # WH_KEYBOARD_LL requires hMod=NULL when the hook proc lives in the
        # current process (python.exe). Passing GetModuleHandleW(None) yields
        # error 126 ("module not found") and the Alt+Space fallback never starts.
        hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            ctypes.cast(self._proc_ref, ctypes.c_void_p),
            wintypes.HINSTANCE(0),
            0,
        )
        if not hook:
            err = kernel32.GetLastError()
            logger.warning("%s low-level hook failed (error=%s)", self._label, err)
            self._ready.set()
            return

        hook_handle.append(hook)
        self._hook = hook
        self._registered = True
        logger.info("%s registered via low-level keyboard hook", self._label)
        self._ready.set()

        msg = MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if result == 0 or result == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.UnhookWindowsHookEx(hook)
        except Exception:
            pass
        self._hook = None
        self._registered = False


def start_hotkey_listener(
    label: str,
    callback: Callable[[], None],
    *,
    allow_low_level: bool = True,
) -> tuple[Win32HotkeyListener | LowLevelHotkeyListener | None, str]:
    """Try RegisterHotKey, then fall back to a low-level hook."""
    win32_listener = Win32HotkeyListener()
    if win32_listener.start(label, callback):
        return win32_listener, "win32"

    if not allow_low_level:
        return None, ""

    ll_listener = LowLevelHotkeyListener()
    if ll_listener.start(label, callback):
        return ll_listener, "lowlevel"

    return None, ""