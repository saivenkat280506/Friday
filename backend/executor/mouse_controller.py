"""
mouse_controller.py — FRIDAY Computer Controller
Full agentic control: mouse, keyboard, screen, windows, clipboard, apps.
"""

import asyncio
import base64
import io
import logging
import os
import platform
import time
from typing import Optional

import pyautogui
import pyperclip

logger = logging.getLogger("friday.control")

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05
SCREEN_W, SCREEN_H = pyautogui.size()

_MAX_ACTIONS_PER_SEC = 10
_action_times: list[float] = []
_ocr_disabled = False


def _rate_limit():
    now = time.monotonic()
    global _action_times
    _action_times = [t for t in _action_times if now - t < 1.0]
    if len(_action_times) >= _MAX_ACTIONS_PER_SEC:
        raise RuntimeError("Action rate limit exceeded.")
    _action_times.append(now)


def _safe_coords(x: int, y: int) -> tuple[int, int]:
    margin = 5
    return max(margin, min(SCREEN_W - margin, x)), max(margin, min(SCREEN_H - margin, y))


APP_SHORTCUTS: dict[str, str] = {
    "chrome": "chrome", "google chrome": "chrome", "firefox": "firefox",
    "edge": "msedge", "brave": "brave",
    "whatsapp": "whatsapp", "telegram": "telegram", "discord": "discord",
    "slack": "slack", "zoom": "zoom", "teams": "msteams",
    "vscode": "code", "vs code": "code",
    "terminal": "wt" if platform.system() == "Windows" else "gnome-terminal",
    "cmd": "cmd", "powershell": "powershell", "git bash": "git-bash",
    "pycharm": "pycharm", "android studio": "studio",
    "spotify": "spotify", "vlc": "vlc", "obs": "obs64",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "notepad": "notepad", "notepad++": "notepad++", "paint": "mspaint",
    "explorer": "explorer", "files": "explorer", "file manager": "explorer",
    "task manager": "taskmgr", "settings": "ms-settings:", "control panel": "control",
    "calculator": "calc", "snipping tool": "snippingtool",
}


class ComputerController:
    async def mouse_move(self, x: int, y: int, duration: float = 0.2) -> dict:
        _rate_limit()
        x, y = _safe_coords(x, y)
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=duration)
        return {"status": "success", "x": x, "y": y}

    async def mouse_click(self, x: int | None = None, y: int | None = None,
                          button: str = "left", double: bool = False,
                          location_name: str | None = None) -> dict:
        _rate_limit()
        if location_name and (x is None or y is None):
            found = await self.find_on_screen(location_name)
            if found:
                x, y = found
            else:
                return {"status": "failed", "error": f"Could not find '{location_name}' on screen"}
        if x is None or y is None:
            x, y = pyautogui.position()
        x, y = _safe_coords(int(x), int(y))
        clicks = 2 if double else 1
        await asyncio.to_thread(pyautogui.click, x, y, clicks=clicks, button=button)
        return {"status": "success", "message": f"{'Double-c' if double else 'C'}licked at ({x}, {y})"}

    async def mouse_right_click(self, x: int, y: int) -> dict:
        return await self.mouse_click(x, y, button="right")

    async def mouse_scroll(self, amount: int = 3, direction: str = "down") -> dict:
        _rate_limit()
        clicks = amount if direction == "up" else -amount
        await asyncio.to_thread(pyautogui.scroll, clicks)
        return {"status": "success", "message": f"Scrolled {direction} {amount}x"}

    async def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> dict:
        _rate_limit()
        sx, sy = _safe_coords(start_x, start_y)
        ex, ey = _safe_coords(end_x, end_y)
        await asyncio.to_thread(pyautogui.drag, ex - sx, ey - sy, duration=duration, button="left")
        return {"status": "success", "message": f"Dragged from ({sx},{sy}) to ({ex},{ey})"}

    async def get_mouse_position(self) -> dict:
        x, y = pyautogui.position()
        return {"x": x, "y": y}

    async def keyboard_type(self, text: str, interval: float = 0.02, params: dict | None = None) -> dict:
        _rate_limit()
        if not text:
            return {"status": "skipped", "message": "Empty text"}
        from executor.window_context import validate_typing_context
        check = validate_typing_context(params or {}, intent="type_text")
        if check.get("status") != "ok":
            return {"status": "failed", "error": check.get("message", "Wrong text field focused")}
        # Select all and delete before typing to clear the text field
        await asyncio.to_thread(pyautogui.hotkey, "ctrl", "a")
        await asyncio.sleep(0.05)
        await asyncio.to_thread(pyautogui.press, "delete")
        await asyncio.sleep(0.05)
        await asyncio.to_thread(pyautogui.typewrite, text, interval=interval)
        return {"status": "success", "message": f"Typed '{text[:40]}'"}

    async def keyboard_hotkey(self, *keys: str) -> dict:
        _rate_limit()
        if not keys:
            return {"status": "skipped", "message": "No keys provided"}
        await asyncio.to_thread(pyautogui.hotkey, *keys)
        return {"status": "success", "message": f"Pressed {'+'.join(keys)}"}

    async def keyboard_press(self, key: str, presses: int = 1) -> dict:
        _rate_limit()
        await asyncio.to_thread(pyautogui.press, key, presses=presses)
        return {"status": "success", "message": f"Pressed {key} x {presses}"}

    async def keyboard_hold(self, key: str, duration: float = 0.5) -> dict:
        _rate_limit()
        await asyncio.to_thread(pyautogui.keyDown, key)
        await asyncio.sleep(duration)
        await asyncio.to_thread(pyautogui.keyUp, key)
        return {"status": "success", "message": f"Held {key} for {duration}s"}

    async def hotkey_from_string(self, combo: str) -> dict:
        keys = [k.strip().lower() for k in combo.replace("+", " ").split()]
        return await self.keyboard_hotkey(*keys)

    async def take_screenshot(self, region: tuple | None = None, save_path: str | None = None) -> dict:
        _rate_limit()
        img = await asyncio.to_thread(pyautogui.screenshot, region=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        if save_path:
            img.save(save_path)
        return {"status": "success", "base64_png": b64, "width": img.width, "height": img.height,
                "saved_path": save_path, "message": "Screenshot captured"}

    async def capture_screen_text(self, region: str | tuple | None = None) -> str:
        if platform.system() == "Darwin":
            try:
                def _mac_vision_ocr():
                    import Quartz
                    import Vision

                    if isinstance(region, tuple) and len(region) == 4:
                        x, y, w, h = region
                        cg_rect = Quartz.CGRectMake(x, y, w, h)
                    else:
                        cg_rect = Quartz.CGRectInfinite

                    image_ref = Quartz.CGWindowListCreateImage(
                        cg_rect,
                        Quartz.kCGWindowListOptionOnScreenOnly,
                        Quartz.kCGNullWindowID,
                        Quartz.kCGWindowImageDefault,
                    )
                    if not image_ref:
                        return ""
                    req = Vision.VNRecognizeTextRequest.alloc().init()
                    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
                    req.setUsesLanguageCorrection_(True)
                    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image_ref, None)
                    success = handler.performRequests_error_([req], None)
                    if not success:
                        return ""
                    results = req.results()
                    lines = []
                    for obs in results:
                        top = obs.topCandidates_(1)
                        if top:
                            lines.append(top[0].string())
                    return "\n".join(lines).strip()

                text = await asyncio.to_thread(_mac_vision_ocr)
                if text:
                    return text
            except Exception as e:
                logger.debug("macOS Vision OCR error: %s", e)

        global _ocr_disabled
        if _ocr_disabled:
            return ""
        try:
            import pytesseract
            from PIL import Image
            if region == "active_window":
                bbox = await self._get_active_window_bbox()
                img = await asyncio.to_thread(pyautogui.screenshot, region=bbox)
            elif isinstance(region, tuple):
                img = await asyncio.to_thread(pyautogui.screenshot, region=region)
            else:
                img = await asyncio.to_thread(pyautogui.screenshot)
            return (await asyncio.to_thread(pytesseract.image_to_string, img, config="--psm 3")).strip()
        except ImportError:
            logger.debug("pytesseract not installed")
            return ""
        except Exception as e:
            if "tesseract" in str(e).lower():
                _ocr_disabled = True
                logger.info("Tesseract unavailable — OCR disabled for this session.")
            else:
                logger.warning(f"OCR failed: {e}")
            return ""

    async def find_on_screen(self, image_path_or_description: str, confidence: float = 0.8) -> tuple[int, int] | None:
        global _ocr_disabled
        if _ocr_disabled:
            return None
        if os.path.exists(image_path_or_description):
            try:
                return await asyncio.to_thread(pyautogui.locateCenterOnScreen, image_path_or_description, confidence=confidence)
            except pyautogui.ImageNotFoundException:
                return None
        try:
            import pytesseract
            img = await asyncio.to_thread(pyautogui.screenshot)
            data = await asyncio.to_thread(pytesseract.image_to_data, img, output_type=pytesseract.Output.DICT)
            target = image_path_or_description.lower()
            for i, word in enumerate(data["text"]):
                if target in word.lower() and int(data["conf"][i]) > 50:
                    return (data["left"][i] + data["width"][i] // 2, data["top"][i] + data["height"][i] // 2)
        except Exception:
            pass
        return None

    async def get_active_window_title(self) -> str | None:
        try:
            if platform.system() == "Windows":
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value or None
            elif platform.system() == "Darwin":
                from perception.world import get_world_snapshot
                snap = get_world_snapshot()
                if snap.window_title:
                    return f"{snap.app_display} — {snap.window_title}" if snap.app_display else snap.window_title
                return snap.app_display or None
            else:
                proc = await asyncio.create_subprocess_exec("xdotool", "getactivewindow", "getwindowname",
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await proc.communicate()
                return out.decode().strip() or None
        except Exception as e:
            logger.debug(f"get_active_window_title: {e}")
            return None

    async def _get_active_window_bbox(self) -> tuple[int, int, int, int] | None:
        try:
            if platform.system() == "Windows":
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            pass
        return None

    async def focus_window(self, app_name: str) -> dict:
        try:
            if platform.system() == "Windows":
                import ctypes
                user32 = ctypes.windll.user32
                result = {"found": False}
                def enum_callback(hwnd, _):
                    if user32.IsWindowVisible(hwnd):
                        length = user32.GetWindowTextLengthW(hwnd)
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        if app_name.lower() in buf.value.lower():
                            user32.ShowWindow(hwnd, 9)
                            user32.SetForegroundWindow(hwnd)
                            result["found"] = True
                            return False
                    return True
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
                user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
                if result["found"]:
                    return {"status": "success", "message": f"Focused {app_name}"}
                return {"status": "failed", "error": f"Window '{app_name}' not found"}
            else:
                await asyncio.create_subprocess_exec("xdotool", "search", "--name", app_name, "windowactivate", "--sync")
                return {"status": "success", "message": f"Focused {app_name}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    async def close_window(self, app_name: str) -> dict:
        if platform.system() == "Darwin":
            if app_name and app_name.lower() not in ("window", "this", "active", "it", "all", "all tabs"):
                clean_app = app_name.strip()
                script = f'tell application "{clean_app}" to quit'
                res = await asyncio.to_thread(subprocess.run, ["osascript", "-e", script], capture_output=True, text=True)
                if res.returncode == 0:
                    return {"status": "success", "message": f"Closed {clean_app}"}
            if "tab" in (app_name or "").lower():
                return await self.keyboard_hotkey("cmd", "option", "w")
            return await self.keyboard_hotkey("cmd", "w")
        elif platform.system() == "Windows":
            await self.focus_window(app_name)
            await asyncio.sleep(0.3)
            return await self.keyboard_hotkey("alt", "f4")
        else:
            await self.focus_window(app_name)
            await asyncio.sleep(0.3)
            return await self.keyboard_hotkey("ctrl", "q")

    async def clipboard_copy(self, text: str | None = None) -> dict:
        if text:
            await asyncio.to_thread(pyperclip.copy, text)
            return {"status": "success", "message": "Text copied to clipboard"}
        return await self.keyboard_hotkey("ctrl", "c")

    async def clipboard_paste(self) -> dict:
        return await self.keyboard_hotkey("ctrl", "v")

    async def clipboard_read(self) -> str:
        try:
            return await asyncio.to_thread(pyperclip.paste)
        except Exception:
            return ""

    async def open_app(self, app_name: str) -> dict:
        from executor.open_app import open_app as launch_app

        ok, msg = await asyncio.to_thread(launch_app, app_name)
        if ok:
            return {"status": "success", "message": msg}
        return {"status": "failed", "error": msg}

    async def _open_url_app(self, name: str) -> dict:
        from executor.open_app import WEB_APP_URLS, _normalize_app_name

        url = WEB_APP_URLS.get(_normalize_app_name(name))
        if url:
            import webbrowser
            await asyncio.to_thread(webbrowser.open, url)
            return {"status": "success", "message": f"Opened {name} in browser"}
        return {"status": "failed", "error": f"No URL for {name}"}

    async def _launch_process(self, cmd: str, display_name: str) -> dict:
        try:
            if platform.system() == "Windows":
                proc = await asyncio.create_subprocess_shell(f'start "" "{cmd}"',
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            else:
                proc = await asyncio.create_subprocess_exec(cmd,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode and proc.returncode != 0:
                return {"status": "failed", "error": err.decode().strip() if err else "unknown"}
            return {"status": "success", "message": f"Launched {display_name}"}
        except asyncio.TimeoutError:
            return {"status": "success", "message": f"Launched {display_name}"}
        except FileNotFoundError:
            return {"status": "failed", "error": f"I couldn't find {display_name}."}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    async def get_screen_size(self) -> dict:
        return {"width": SCREEN_W, "height": SCREEN_H}

    async def move_to_center(self) -> dict:
        return await self.mouse_move(SCREEN_W // 2, SCREEN_H // 2)


_scroll = ComputerController().mouse_scroll

def scroll(amount: int = 3, direction: str = "down") -> tuple:
    import asyncio
    try:
        result = asyncio.run(_scroll(amount, direction))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Scroll failed: {e}"


def click(x: Optional[int] = None, y: Optional[int] = None) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().mouse_click(x, y))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Click failed: {e}"


def move_to(x: int, y: int) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().mouse_move(x, y))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Move failed: {e}"


def type_text(text: str, interval: float = 0.02, params: dict | None = None) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().keyboard_type(text, interval, params=params))
        if result["status"] == "success":
            return True, result.get("message", "")
        return False, result.get("error", result.get("message", "Type failed"))
    except Exception as e:
        return False, f"Type failed: {e}"


def hotkey(*keys) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().keyboard_hotkey(*keys))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Hotkey failed: {e}"


def double_click(x: Optional[int] = None, y: Optional[int] = None) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().mouse_click(x, y, double=True))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Double-click failed: {e}"


def press_key(key: str) -> tuple:
    import asyncio
    try:
        result = asyncio.run(ComputerController().keyboard_press(key))
        return result["status"] == "success", result.get("message", "")
    except Exception as e:
        return False, f"Key press failed: {e}"
