"""
tool_handlers.py — Low-level FRIDAY tool execution implementations.

Handlers are bound to tool names by ``tools_registry.ToolRegistry``.
Async metadata lives in ``executor/tools/<category>.py``; sync metadata in
``skills_registry.json``. This file contains implementations only.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
import os
import platform
import re
import subprocess
import webbrowser
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, TypeAlias

if TYPE_CHECKING:
    from brain.state import AgentState

logger = logging.getLogger("friday.tools")

ToolResult: TypeAlias = dict[str, Any]
SyncToolResult: TypeAlias = tuple[bool, str]
AsyncHandler: TypeAlias = Callable[[str, dict[str, Any], "AgentState"], Awaitable[ToolResult]]
SyncHandler: TypeAlias = Callable[[dict[str, Any]], SyncToolResult]


class AsyncToolHandlers:
    """Async tool implementations used by the LangGraph pipeline."""

    def __init__(self) -> None:
        from executor.mouse_controller import ComputerController

        self._ctrl = ComputerController()

    async def mouse_click(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        parts = [p.strip() for p in raw.split(":")]
        loc = parts[0] if parts[0] else None
        btn = params.get("button", "left")
        dbl = params.get("double", False)
        if loc and not loc.replace("-", "").replace(",", "").strip().isdigit():
            return await self._ctrl.mouse_click(location_name=loc, button=btn, double=dbl)
        x, y = params.get("x"), params.get("y")
        if x is None or y is None:
            try:
                coords = raw.split(",")
                x, y = int(coords[0]), int(coords[1])
            except Exception:
                x, y = None, None
        return await self._ctrl.mouse_click(x=x, y=y, button=btn, double=dbl)

    async def mouse_move(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        try:
            parts = raw.split(",")
            x, y = int(parts[0]), int(parts[1])
        except Exception:
            x, y = params.get("x", 0), params.get("y", 0)
        return await self._ctrl.mouse_move(int(x), int(y))

    async def mouse_scroll(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        parts = raw.split(":")
        direction = parts[0] if parts[0] else params.get("direction", "down")
        try:
            amount = int(parts[1]) if len(parts) > 1 else params.get("amount", 3)
        except Exception:
            amount = 3
        return await self._ctrl.mouse_scroll(amount=int(amount), direction=direction)

    async def mouse_drag(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        try:
            sx, sy, ex, ey = [int(v) for v in raw.split(",")]
        except Exception:
            return {"status": "failed", "error": "Drag needs sx,sy,ex,ey format"}
        return await self._ctrl.mouse_drag(sx, sy, ex, ey)

    async def keyboard_type(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        text = raw or params.get("text", "")
        return await self._ctrl.keyboard_type(text)

    async def keyboard_hotkey(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        if raw:
            keys = [k.strip() for k in raw.replace("+", ",").split(",") if k.strip()]
        else:
            keys = params.get("keys", [])
        if not keys:
            return {"status": "failed", "error": "No keys specified"}
        return await self._ctrl.keyboard_hotkey(*keys)

    async def keyboard_press(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        key = raw or params.get("key", "enter")
        return await self._ctrl.keyboard_press(key)

    async def screen_capture(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_at_%I.%M.%S_%p")
        desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        path = os.path.join(desktop_dir, f"Screenshot_{ts}.png")
        res = await self._ctrl.take_screenshot(save_path=path)
        if res.get("status") == "success":
            return {
                "status": "success",
                "result": path,
                "saved_path": path,
                "message": f"Captured screenshot and saved it to your Desktop.",
            }
        return res

    async def screen_read(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        # 1. Desktop perception snapshot
        from perception.world import get_world_snapshot
        snap = get_world_snapshot()
        app_name = snap.app_display or snap.app or "your desktop"
        window_title = snap.window_title or ""

        # 2. UI elements from accessibility driver
        ui_elements_desc = ""
        try:
            from executor.mac_ax import get_ui_elements
            elements = get_ui_elements()
            if elements:
                names = [e.title for e in elements if e.title and len(e.title) < 30]
                if names:
                    ui_elements_desc = f" with buttons: {', '.join(names[:5])}"
        except Exception:
            pass

        # 3. Optional OCR text if available
        ocr_text = await self._ctrl.capture_screen_text()
        if ocr_text:
            lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
            substantive = [l for l in lines if len(l) > 20 and not l.startswith("http")]
            readable = " ".join(substantive[:6]) if substantive else " ".join(lines[:6])
            if len(readable) > 280:
                readable = readable[:277] + "..."
            msg = f"On your screen: {readable}"
            return {
                "status": "success",
                "result": ocr_text,
                "message": msg,
                "app": app_name,
                "window_title": window_title,
                "content": readable,
            }

        # 4. Perception description
        if window_title:
            msg = f"You are currently in {app_name}, viewing '{window_title}'{ui_elements_desc}."
        elif app_name:
            msg = f"You have {app_name} open on your screen{ui_elements_desc}."
        else:
            msg = "I can see your desktop workspace."

        return {"status": "success", "result": msg, "message": msg}

    async def find_on_screen(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        target = raw or params.get("target", "")
        result = await self._ctrl.find_on_screen(target)
        if result:
            return {"status": "success", "x": result[0], "y": result[1]}
        return {"status": "failed", "error": f"'{target}' not found on screen"}

    async def window_focus(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        app = raw or params.get("app", "")
        return await self._ctrl.focus_window(app)

    async def window_close(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        app = raw or params.get("app", "")
        return await self._ctrl.close_window(app)

    async def clipboard_copy(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        text = raw if raw else None
        return await self._ctrl.clipboard_copy(text)

    async def clipboard_paste(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        return await self._ctrl.clipboard_paste()

    async def clipboard_read(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        text = await self._ctrl.clipboard_read()
        return {"status": "success", "result": text, "message": f"Clipboard: {text[:100]}"}

    async def open_app(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        app = raw or params.get("app", "")
        return await self._ctrl.open_app(app)

    async def open_vscode_new_project(
        self, raw: str, params: dict[str, Any], state: AgentState
    ) -> ToolResult:
        from executor.open_app import open_vscode_new_project

        name = (raw or params.get("project_name") or "").strip()
        ok, msg = await asyncio.to_thread(open_vscode_new_project, name)
        if ok:
            return {"status": "success", "message": msg}
        return {"status": "failed", "error": msg}

    async def search_web(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        query = raw or params.get("query", "")
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        await asyncio.to_thread(webbrowser.open, url)
        return {"status": "success", "message": f"Searching for '{query}'"}

    async def smart_tab_cleanup(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        close_kw = params.get("close_keywords", [])
        keep_kw = params.get("keep_keywords", [])
        if raw and not close_kw and not keep_kw:
            close_kw = [raw.strip()]
        from executor.automation import smart_cleanup_tabs
        count = await asyncio.to_thread(smart_cleanup_tabs, close_keywords=close_kw, keep_keywords=keep_kw)
        return {"status": "success", "message": f"Closed {count} unnecessary browser tab(s)."}

    async def open_youtube(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        query = (raw or params.get("query") or "").strip()
        if re.fullmatch(r"(?:you\s*tube|youtube)[.!?,;:]*", query, re.I):
            query = ""
        url = (
            f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
            if query
            else "https://www.youtube.com"
        )
        try:
            from executor.automation import open_url_in_chrome
            opened = await asyncio.to_thread(open_url_in_chrome, url)
        except Exception:
            opened = False
        if not opened:
            await asyncio.to_thread(webbrowser.open, url)
        return {"status": "success", "message": "Opened YouTube." if not query else f"Opened YouTube for {query}."}

    async def send_whatsapp_message(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.whatsapp_handler import send_whatsapp_message as wa_send

        contact = (params.get("contact") or "").strip()
        message = (params.get("message") or "").strip()

        # Pass enhanced_params through so the handler can use
        # pre-resolved phone numbers and search queries.
        enhanced = {
            k: params[k]
            for k in (
                "phone_number",
                "display_name",
                "contact_aliases",
                "search_strategy",
                "search_queries",
                "match_needles",
            )
            if k in params
        } or None

        if not contact:
            from executor.automation import open_whatsapp

            ok, status = await asyncio.to_thread(open_whatsapp)
            if ok:
                return {"status": "success", "message": status}
            return {"status": "failed", "error": status, "message": status}

        result = await wa_send(contact, message, enhanced_params=enhanced)
        if result["success"]:
            contact_name = result.get("contact", contact)
            if result.get("stage") == "draft_ready":
                msg = f"I've typed the message to {contact_name}. Shall I send it now, Boss?"
            elif not message:
                msg = f"Opened chat with {contact_name} on WhatsApp."
            else:
                msg = f"Message sent to {contact_name} on WhatsApp."
            return {
                "status": "success",
                "message": msg,
            }
        error = result.get("error") or "Failed to access WhatsApp."
        return {"status": "failed", "error": error, "message": error}

    async def confirm_whatsapp_send(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        import platform, subprocess
        if platform.system() == "Darwin":
            script = '''
            tell application "WhatsApp" to activate
            delay 0.2
            tell application "System Events"
                tell process "WhatsApp"
                    key code 36
                end tell
            end tell
            '''
            try:
                subprocess.run(["osascript", "-e", script], check=True, timeout=5)
                return {"status": "success", "message": "Message sent on WhatsApp, Boss."}
            except Exception as e:
                return {"status": "failed", "error": str(e), "message": "Failed to send message."}
        else:
            import pyautogui
            pyautogui.press("enter")
            return {"status": "success", "message": "Message sent on WhatsApp, Boss."}

    async def volume_set(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.local_music_player import (
            adjust_volume,
            get_playback_state,
            get_volume,
            set_volume,
        )

        parts = raw.split(":")
        direction = parts[1] if len(parts) > 1 else params.get("direction", "set")
        try:
            level = int(parts[0]) if parts[0].isdigit() else params.get("level")
        except Exception:
            level = None

        if get_playback_state().get("has_track"):
            if direction == "up":
                applied = await asyncio.to_thread(adjust_volume, 0.1)
                return {"status": "success", "message": f"Music volume up ({int(applied * 100)}%)"}
            if direction == "down":
                applied = await asyncio.to_thread(adjust_volume, -0.1)
                return {"status": "success", "message": f"Music volume down ({int(applied * 100)}%)"}
            if direction == "reduce" and level is not None:
                current = int(await asyncio.to_thread(get_volume) * 100)
                target = max(0, current - int(level))
                applied = await asyncio.to_thread(set_volume, target / 100.0)
                return {
                    "status": "success",
                    "message": f"Music volume reduced to {int(applied * 100)}%",
                }
            if direction == "increase" and level is not None:
                current = int(await asyncio.to_thread(get_volume) * 100)
                target = min(100, current + int(level))
                applied = await asyncio.to_thread(set_volume, target / 100.0)
                return {
                    "status": "success",
                    "message": f"Music volume increased to {int(applied * 100)}%",
                }
            if level is not None:
                applied = await asyncio.to_thread(set_volume, level / 100.0)
                return {"status": "success", "message": f"Music volume set to {int(applied * 100)}%"}

        if platform.system() == "Windows":
            if direction == "up":
                for _ in range(5):
                    await self._ctrl.keyboard_press("volumeup")
                return {"status": "success", "message": "Volume up"}
            if direction == "down":
                for _ in range(5):
                    await self._ctrl.keyboard_press("volumedown")
                return {"status": "success", "message": "Volume down"}
            if level is not None:
                try:
                    await asyncio.create_subprocess_exec(
                        "nircmd", "setsysvolume", str(int(level / 100 * 65535))
                    )
                    return {"status": "success", "message": f"Volume set to {level}%"}
                except FileNotFoundError:
                    return {"status": "failed", "error": "nircmd not found"}
        elif level is not None:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"
            )
            await proc.wait()
            return {"status": "success", "message": f"Volume set to {level}%"}
        return {"status": "partial", "message": "Volume command sent"}

    async def volume_mute(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        if platform.system() == "Windows":
            await self._ctrl.keyboard_press("volumemute")
        else:
            await asyncio.create_subprocess_exec("pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle")
        return {"status": "success", "message": "Volume toggled"}

    async def system_info(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            info = (
                f"CPU: {cpu}% | RAM: {ram.percent}% used "
                f"({ram.available // (1024**3):.1f} GB free) | Disk: {disk.percent}% used "
                f"({disk.free // (1024**3):.0f} GB free)"
            )
            return {"status": "success", "result": info, "message": info}
        except ImportError:
            return {"status": "failed", "error": "psutil not installed"}

    async def system_shutdown(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        os.system("shutdown /s /t 30" if platform.system() == "Windows" else "shutdown -h +1")
        return {"status": "success", "message": "Shutting down in 30 seconds."}

    async def system_restart(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        os.system("shutdown /r /t 30" if platform.system() == "Windows" else "shutdown -r +1")
        return {"status": "success", "message": "Restarting in 30 seconds."}

    async def system_sleep(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        if platform.system() == "Windows":
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        else:
            os.system("systemctl suspend")
        return {"status": "success", "message": "Going to sleep."}

    async def system_lock(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        if platform.system() == "Windows":
            os.system("rundll32.exe user32.dll,LockWorkStation")
        else:
            os.system("loginctl lock-session")
        return {"status": "success", "message": "Screen locked."}

    async def _browser_media(self, action: str) -> ToolResult:
        from executor.spotify_control import is_spotify_running, next_track, play_pause, previous_track

        if is_spotify_running():
            if action == "play":
                ok, msg = await asyncio.to_thread(play_pause)
            elif action == "pause":
                ok, msg = await asyncio.to_thread(play_pause)
            elif action == "next":
                ok, msg = await asyncio.to_thread(next_track)
            elif action == "prev":
                ok, msg = await asyncio.to_thread(previous_track)
            else:
                ok, msg = False, "Unknown media action"
            if ok:
                return {"status": "success", "message": msg}

        from executor.browser_agent import run_browser_recipe

        ok, msg = await run_browser_recipe(
            "mediaControl",
            {"action": action},
            task=f"media {action}",
            mode="headed",
        )
        if ok:
            return {"status": "success", "message": msg}
        key_map = {"play": "playpause", "pause": "playpause", "next": "nexttrack", "prev": "prevtrack"}
        await self._ctrl.keyboard_press(key_map.get(action, "playpause"))
        return {"status": "success", "message": f"Media {action} via system keys (browser fallback)"}

    async def media_play(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.local_music_player import get_playback_state, resume
        from services.companion_state import update_music_playback

        if get_playback_state().get("has_track"):
            ok, song = await asyncio.to_thread(resume)
            if ok:
                await update_music_playback(is_playing=True, song=song)
                return {"status": "success", "message": "Resumed local playback."}
        return await self._browser_media("play")

    async def play_music(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.music_player import DEFAULT_BARE_PLATFORM, DEFAULT_PLATFORM, play_music

        parts = raw.split(":", 1)
        song = parts[0] if parts and parts[0] else params.get("song", "")
        music_platform = parts[1] if len(parts) > 1 else params.get("platform", "")
        if not song:
            music_platform = DEFAULT_BARE_PLATFORM
            song = ""
        elif not music_platform:
            music_platform = params.get("platform") or DEFAULT_BARE_PLATFORM
        from brain.memory import save_memory

        success, message = await asyncio.to_thread(play_music, song, music_platform)
        if success:
            save_memory("last_song", song)
            from services.companion_state import set_music_task

            display_song = song.strip()
            if not display_song:
                from executor.local_music_player import get_playback_state

                display_song = get_playback_state().get("song", "") or "Music"
            elif music_platform != "local" and not display_song:
                display_song = "Music"
            await set_music_task(
                song=display_song,
                platform=music_platform,
                is_playing=True,
            )
        status = "success" if success else "failed"
        return {"status": status, "message": message, "error": None if success else message}

    async def media_pause(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.local_music_player import get_playback_state, pause, stop
        from executor.spotify_control import is_spotify_running, play_pause
        from services.companion_state import set_idle_task, update_music_playback

        intent = (state.get("cleaned_input") or raw or "").lower()
        is_stop = bool(re.search(r"\bstop\b", intent)) and bool(
            re.search(r"\b(music|song|track|playback|spotify)\b", intent) or intent.strip() == "stop"
        )

        if get_playback_state().get("has_track"):
            if is_stop:
                await asyncio.to_thread(stop)
                await set_idle_task()
                return {"status": "success", "message": "Stopped local playback."}
            ok, song = await asyncio.to_thread(pause)
            if ok:
                await update_music_playback(is_playing=False, song=song)
                return {"status": "success", "message": "Paused local playback."}

        if is_spotify_running():
            ok, msg = await asyncio.to_thread(play_pause)
            if ok:
                if is_stop:
                    await set_idle_task()
                return {"status": "success", "message": msg}

        result = await self._browser_media("pause")
        if is_stop and result.get("status") == "success":
            await set_idle_task()
        return result

    async def media_next(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.local_music_player import get_playback_state, next_track
        from services.companion_state import update_music_playback

        if get_playback_state().get("has_track"):
            ok, song = await asyncio.to_thread(next_track)
            if ok:
                await update_music_playback(is_playing=True, song=song)
                return {"status": "success", "message": f"Next track: {song}"}
        result = await self._browser_media("next")
        if result.get("status") == "success" and "fallback" not in result.get("message", ""):
            return result
        await self._ctrl.keyboard_press("nexttrack")
        return {"status": "success", "message": "Next track"}

    async def media_prev(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from executor.local_music_player import get_playback_state, previous_track
        from services.companion_state import update_music_playback

        if get_playback_state().get("has_track"):
            ok, song = await asyncio.to_thread(previous_track)
            if ok:
                await update_music_playback(is_playing=True, song=song)
                return {"status": "success", "message": f"Previous track: {song}"}
        result = await self._browser_media("prev")
        if result.get("status") == "success" and "fallback" not in result.get("message", ""):
            return result
        await self._ctrl.keyboard_press("prevtrack")
        return {"status": "success", "message": "Previous track"}

    async def time_date(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        from services.companion_state import set_flash_task

        now = datetime.datetime.now()
        lower = (raw or state.get("cleaned_input") or "").lower()
        if "date" in lower or "day" in lower:
            title = now.strftime("%A, %B %d")
            detail = now.strftime("%Y")
        else:
            title = now.strftime("%I:%M %p")
            detail = now.strftime("%A, %B %d")
        msg = now.strftime("It's %I:%M %p on %A, %B %d, %Y")
        await set_flash_task(title, detail, seconds=5.0)
        return {"status": "success", "result": msg, "message": msg}

    async def timer_set(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        parts = raw.split(":")
        try:
            duration = int(parts[0]) if parts[0] else params.get("duration", 1)
            unit = parts[1] if len(parts) > 1 else params.get("unit", "minute")
        except Exception:
            duration, unit = 1, "minute"
        seconds = duration * {"second": 1, "minute": 60, "hour": 3600}.get(unit, 60)
        msg = f"Timer set for {duration} {unit}{'s' if duration != 1 else ''}."
        asyncio.create_task(self._fire_timer_alert(seconds, msg))
        return {"status": "success", "message": msg}

    async def _fire_timer_alert(self, seconds: float, label: str) -> None:
        await asyncio.sleep(seconds)
        logger.info("TIMER DONE: %s", label)
        if platform.system() == "Windows":
            try:
                from win10toast import ToastNotifier

                ToastNotifier().show_toast("FRIDAY Timer", label, duration=5)
            except Exception:
                pass

    async def calculate(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        try:
            expr = raw or state.get("cleaned_input", "")
            allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
            result = eval(expr, {"__builtins__": {}}, allowed)
            msg = f"{expr} = {result}"
            return {"status": "success", "result": result, "message": msg}
        except Exception as exc:
            return {"status": "failed", "error": f"Calculation error: {exc}"}

    async def chat(self, raw: str, params: dict[str, Any], state: AgentState) -> ToolResult:
        return {"status": "success", "result": None, "message": "chat"}


def build_async_handler_map(
    handlers: AsyncToolHandlers,
    tool_names: Iterable[str],
) -> dict[str, AsyncHandler]:
    """Wire async handler callables to tool names."""
    return {
        name: getattr(handlers, name)
        for name in tool_names
        if hasattr(handlers, name)
    }


class LegacySyncHandlers:
    """
    Synchronous tool implementations for the JSON intent executor.

    WhatsApp delivery delegates to ``whatsapp_handler`` (canonical path).
    """

    @staticmethod
    def build() -> dict[str, SyncHandler]:
        try:
            from executor.automation import read_news_headlines, smart_search
            from executor.music_player import (
                DEFAULT_BARE_PLATFORM,
                play_music,
                play_on_spotify,
                play_on_youtube,
                play_on_youtube_music,
            )
            from executor.whatsapp_handler import send_whatsapp_message_sync as wa_send_message
            from executor.task_manager import task_manager
            from executor.mouse_controller import scroll, click, hotkey, move_to, type_text
            from executor.open_app import open_app, open_vscode_new_project
        except ImportError:
            return {}

        import urllib.parse
        import pyautogui

        def _legacy_whatsapp_result(result: dict[str, Any]) -> SyncToolResult:
            if result.get("success"):
                contact = result.get("contact", "")
                return True, f"Message sent to {contact} on WhatsApp."
            return False, result.get("error") or "Failed to send WhatsApp message."

        def search_browser_helper(params: dict[str, Any]) -> SyncToolResult:
            query = params.get("query", "latest news")
            try:
                from executor.browser_agent import run_browser_recipe
                ok, msg = asyncio.run(
                    run_browser_recipe("googleSearch", {"query": query}, task=query, mode="headed")
                )
                if ok:
                    return True, msg
            except Exception as exc:
                logger.debug("Browser recipe search failed: %s", exc)
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            webbrowser.open(url)
            return True, f"Opened browser and searched for '{query}'"

        def cancel_task(params: dict[str, Any]) -> SyncToolResult:
            task_type = params.get("task_type", "all")
            if task_type == "all":
                count = 0
                for tid in list(task_manager.active_tasks.keys()):
                    if task_manager.cancel_task(tid):
                        count += 1
                return (True, f"Stopped {count} task(s)") if count > 0 else (False, "No active tasks")
            count = task_manager.cancel_task_by_type(task_type)
            return (True, f"Stopped {count} task(s)") if count > 0 else (False, f"No active {task_type} tasks")

        def screenshot_helper(params: dict[str, Any]) -> SyncToolResult:
            import base64
            import time as time_mod
            from vision.capture import capture_screen_base64

            try:
                b64 = capture_screen_base64(draw_boxes=False)
                desktop = os.path.join(
                    os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop"
                )
                filename = f"friday_screenshot_{int(time_mod.time())}.png"
                with open(os.path.join(desktop, filename), "wb") as handle:
                    handle.write(base64.b64decode(b64))
                return True, f"Screenshot saved to Desktop as {filename}"
            except Exception as exc:
                return False, f"Screenshot failed: {exc}"

        def volume_helper(params: dict[str, Any]) -> SyncToolResult:
            from executor.local_music_player import adjust_volume, get_playback_state, set_volume

            action = params.get("action", "mute").lower()
            amount = int(params.get("amount", 5))
            level = params.get("level")
            try:
                if get_playback_state().get("has_track"):
                    if action == "up":
                        applied = adjust_volume(0.1 * amount)
                        return True, f"Music volume up ({int(applied * 100)}%)."
                    if action == "down":
                        applied = adjust_volume(-0.1 * amount)
                        return True, f"Music volume down ({int(applied * 100)}%)."
                    if level is not None:
                        applied = set_volume(float(level) / 100.0)
                        return True, f"Music volume set to {int(applied * 100)}%."

                if action == "mute":
                    pyautogui.press("volumemute")
                elif action == "up":
                    for _ in range(amount):
                        pyautogui.press("volumeup")
                elif action == "down":
                    for _ in range(amount):
                        pyautogui.press("volumedown")
                elif action == "unmute":
                    pyautogui.press("volumemute")
                return True, f"Volume {action} done."
            except Exception as exc:
                return False, f"Volume control failed: {exc}"

        def system_command_helper(params: dict[str, Any]) -> SyncToolResult:
            cmd = params.get("command", "").strip()
            allowed_prefixes = [
                "taskmgr",
                "systeminfo",
                "ipconfig",
                "hostname",
                "whoami",
                "date /t",
                "time /t",
                "ver",
                "powercfg /batteryreport",
                "wmic cpu get name",
                "taskkill",
            ]
            if not any(cmd.lower().startswith(prefix) for prefix in allowed_prefixes):
                return False, f"Command not allowed: '{cmd}'"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                output = result.stdout.strip() if result.stdout else "Command executed."
                return True, output[:500]
            except subprocess.TimeoutExpired:
                return False, "Command timed out."
            except Exception as exc:
                return False, f"Command failed: {exc}"

        def browser_agent_helper(params: dict[str, Any]) -> SyncToolResult:
            from executor.browser_agent import run_browser_agent

            task = params.get("task", params.get("description", params.get("query", "")))
            if not task:
                return False, "No task description provided."
            ok, msg = asyncio.run(run_browser_agent(task))
            return ok, msg

        def os_agent_helper(params: dict[str, Any]) -> SyncToolResult:
            from executor.os_agent import run_os_agent

            task = params.get("task", params.get("description", params.get("query", "")))
            if not task:
                return False, "No task description provided."
            result = run_os_agent(task, max_steps=15, use_vision=False)
            success = "SUCCESS" in result or "TASK_COMPLETE" in result
            return success, result

        def search_and_browse_helper(params: dict[str, Any]) -> SyncToolResult:
            query = params.get("query")
            if not query:
                return False, "No search query provided."
            try:
                from executor.browser_agent import run_browser_recipe
                ok, msg = asyncio.run(
                    run_browser_recipe(
                        "searchAndBrowse",
                        {"query": query},
                        task=f"search and browse {query}",
                        mode="headless",
                    )
                )
                if ok:
                    return True, msg
            except Exception as exc:
                logger.debug("Browser searchAndBrowse failed: %s", exc)
            return browser_agent_helper({"task": f"Search for '{query}', open first result, and scroll"})

        return {
            "open_app": lambda params: open_app(params.get("app", "notepad")),
            "open_vscode_new_project": lambda params: open_vscode_new_project(
                params.get("project_name", "")
            ),
            "send_whatsapp": lambda params: _legacy_whatsapp_result(
                wa_send_message(
                    params.get("contact") or params.get("name", ""),
                    params.get("message", ""),
                )
            ),
            "play_youtube": lambda params: play_on_youtube(params.get("song", "")),
            "play_youtube_music": lambda params: play_on_youtube_music(params.get("song", "")),
            "play_spotify_music": lambda params: play_on_spotify(params.get("song", "")),
            "play_music": lambda params: play_music(
                params.get("song", ""),
                params.get("platform") or DEFAULT_BARE_PLATFORM,
            ),
            "search_browser": search_browser_helper,
            "search_and_browse": search_and_browse_helper,
            "read_headlines": lambda params: read_news_headlines(params.get("query", "")),
            "smart_search": lambda params: smart_search(params.get("query", "")),
            "chat": lambda params: (True, "Conversation handled."),
            "mouse_scroll": lambda params: scroll(
                int(params.get("amount", 3)), params.get("direction", "down")
            ),
            "scroll_page": lambda params: scroll(
                int(params.get("amount", 3)), params.get("direction", "down")
            ),
            "screenshot": screenshot_helper,
            "type_text": lambda params: type_text(
                params.get("text", ""),
                float(params.get("interval", 0.02)),
                params=params,
            ),
            "click_at": lambda params: (
                click(params.get("x"), params.get("y"))
                if "x" in params and "y" in params
                else (False, "click_at requires coordinates")
            ),
            "move_mouse": lambda params: (
                move_to(params.get("x"), params.get("y"))
                if "x" in params and "y" in params
                else (False, "move_mouse requires coordinates")
            ),
            "hotkey": lambda params: (
                hotkey(*[k.strip() for k in params.get("keys", "").split("+")])
                if params.get("keys")
                else (False, "No keys specified")
            ),
            "volume_control": volume_helper,
            "system_command": system_command_helper,
            "cancel_task": cancel_task,
            "browser_agent": browser_agent_helper,
            "web_agent": browser_agent_helper,
            "os_control": os_agent_helper,
            "autonomous_task": browser_agent_helper,
        }
