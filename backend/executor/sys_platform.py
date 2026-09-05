"""
sys_platform.py — macOS primitives for FRIDAY
=============================================
This port is macOS-only (AppleScript, `open`, `osascript`, Core Audio).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

IS_MAC = sys.platform == "darwin"
IS_WIN = False
MOD_KEY = "command"

MAC_APP_ALIASES = {
    "notepad": "TextEdit",
    "notepads": "TextEdit",
    "textedit": "TextEdit",
    "notes": "Notes",
    "calculator": "Calculator",
    "calc": "Calculator",
    "paint": "Preview",
    "preview": "Preview",
    "cmd": "Terminal",
    "command prompt": "Terminal",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "powershell": "Terminal",
    "task manager": "Activity Monitor",
    "taskmanager": "Activity Monitor",
    "activity monitor": "Activity Monitor",
    "settings": "System Settings",
    "system settings": "System Settings",
    "system preferences": "System Settings",
    "control panel": "System Settings",
    "file explorer": "Finder",
    "explorer": "Finder",
    "finder": "Finder",
    "files": "Finder",
    "browser": "Safari",
    "safari": "Safari",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "arc": "Arc",
    "firefox": "Firefox",
    "edge": "Microsoft Edge",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "spotify": "Spotify",
    "discord": "Discord",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "slack": "Slack",
    "mail": "Mail",
    "calendar": "Calendar",
    "maps": "Maps",
    "camera": "Photo Booth",
    "photo booth": "Photo Booth",
    "music": "Music",
    "photos": "Photos",
    "youtube": "YouTube",
    "notes": "Notes",
    "apple notes": "Notes",
    "notepad": "Notes",
    "textedit": "TextEdit",
    "word": "Microsoft Word",
    "microsoft word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
}


def desktop_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop")


def current_user() -> str:
    return os.environ.get("USER") or "Unknown"


def python_venv(backend_dir: str) -> str:
    return os.path.join(backend_dir, ".venv", "bin", "python")


def chrome_executable() -> str | None:
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def chrome_user_data() -> str:
    return os.path.expanduser("~/Library/Application Support/Google/Chrome")


def chrome_profile_directory() -> str:
    """Last-used Chrome profile folder (Default, Profile 1, ...)."""
    override = (os.environ.get("CHROME_PROFILE_DIRECTORY") or "").strip()
    if override:
        return override
    try:
        import json

        state = Path(chrome_user_data()) / "Local State"
        data = json.loads(state.read_text(encoding="utf-8"))
        last = str((data.get("profile") or {}).get("last_used") or "Default").strip()
        if last:
            return last
    except Exception:
        pass
    return "Default"


def quit_google_chrome(timeout: float = 8.0) -> None:
    """Ask Chrome to quit so Puppeteer can open the live profile."""
    try:
        osascript('tell application "Google Chrome" to quit', timeout=timeout)
    except Exception:
        pass
    try:
        subprocess.run(["killall", "Google Chrome"], capture_output=True, check=False)
    except Exception:
        pass


def osascript(source: str, timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["osascript", "-e", source],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_open(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["open", *args], capture_output=True, text=True, timeout=10)


def open_uri(uri: str) -> bool:
    try:
        result = run_open(uri)
        return result.returncode == 0
    except Exception:
        return False


def resolve_mac_app(name: str) -> str:
    raw = (name or "").strip().strip("\"'`")
    raw = re.sub(r"[.!?,;:]+$", "", raw).strip()
    key = raw.lower().replace(".exe", "").replace(".app", "")
    if key in MAC_APP_ALIASES:
        return MAC_APP_ALIASES[key]
    if raw.endswith(".app"):
        return raw[:-4]
    return raw


def open_application(app_name: str) -> tuple[bool, str]:
    app = resolve_mac_app(app_name)
    result = run_open("-a", app)
    if result.returncode == 0:
        return True, f"Successfully opened {app}."
    if open_uri(app_name):
        return True, f"Successfully opened {app_name}."
    err = (result.stderr or result.stdout or "").strip()
    return False, f"failed to open {app_name}" + (f": {err}" if err else ". Please make sure it is installed.")


def close_application(app_name: str) -> tuple[bool, str]:
    app = resolve_mac_app(app_name)
    try:
        osascript(f'tell application "{app}" to quit')
    except Exception:
        pass
    subprocess.run(["killall", app], capture_output=True, check=False)
    return True, f"Closed {app}."


def focus_application(app_name: str) -> tuple[bool, str]:
    app = resolve_mac_app(app_name)
    try:
        result = osascript(f'tell application "{app}" to activate')
        if result.returncode == 0:
            return True, f"Focused {app}."
        return False, (result.stderr or f"Could not focus {app}.").strip()
    except Exception as exc:
        return False, str(exc)


def list_visible_windows() -> str:
    script = """
    tell application "System Events"
      set output to ""
      set procs to every process whose visible is true and background only is false
      repeat with p in procs
        set pname to name of p
        try
          repeat with w in windows of p
            set wname to name of w
            set pos to position of w
            set sz to size of w
            set output to output & pname & " | " & wname & " | pos=" & (item 1 of pos as text) & "," & (item 2 of pos as text) & " size=" & (item 1 of sz as text) & "x" & (item 2 of sz as text) & linefeed
          end repeat
        end try
      end repeat
      return output
    end tell
    """
    try:
        result = osascript(script, timeout=6)
        text = (result.stdout or "").strip()
        return text or "No major windows visible on screen."
    except Exception as exc:
        return f"Error scanning UI: {exc}"


def get_work_area() -> dict[str, int]:
    try:
        result = osascript(
            'tell application "Finder" to get bounds of window of desktop',
            timeout=4,
        )
        parts = [int(p.strip()) for p in (result.stdout or "").split(",") if p.strip()]
        if len(parts) == 4:
            left, top, right, bottom = parts
            return {
                "x": left,
                "y": top + 28,
                "width": max(800, right - left),
                "height": max(500, bottom - top - 28),
            }
    except Exception as exc:
        print(f"[Platform] work area AppleScript failed: {exc}")
    try:
        import pyautogui
        width, height = pyautogui.size()
        return {"x": 0, "y": 28, "width": int(width), "height": int(height) - 28}
    except Exception:
        return {"x": 0, "y": 28, "width": 1440, "height": 872}


def set_app_window_bounds(app_hint: str, x: int, y: int, width: int, height: int) -> bool:
    hint = (app_hint or "").replace('"', '\\"')
    script = f"""
    tell application "System Events"
      set matched to false
      repeat with p in (every process whose background only is false)
        set pname to name of p
        if pname contains "{hint}" or pname contains "Electron" or pname contains "FRIDAY" then
          try
            set position of window 1 of p to {{{int(x)}, {int(y)}}}
            set size of window 1 of p to {{{int(width)}, {int(height)}}}
            set matched to true
            exit repeat
          end try
        end if
      end repeat
      return matched
    end tell
    """
    try:
        result = osascript(script, timeout=5)
        return "true" in (result.stdout or "").lower()
    except Exception as exc:
        print(f"[Platform] window snap failed: {exc}")
        return False


def list_process_names() -> set[str]:
    names: set[str] = set()
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            names.add(os.path.basename(raw).lower())
    except Exception as exc:
        print(f"[ProcessMonitor] Error fetching processes: {exc}")
    return names


def pids_matching(pattern: str) -> set[int]:
    pids: set[int] = set()
    try:
        result = subprocess.run(
            ["pgrep", "-if", pattern],
            capture_output=True,
            text=True,
        )
        for token in result.stdout.split():
            if token.isdigit():
                pids.add(int(token))
    except Exception as exc:
        print(f"[Platform] pids_matching failed: {exc}")
    return pids


def is_app_running(app_name: str) -> bool:
    app = resolve_mac_app(app_name)
    if pids_matching(f"^{re.escape(app)}$") or pids_matching(app):
        return True
    try:
        result = osascript(
            f'tell application "System Events" to exists process "{app}"',
            timeout=4,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


_AX_UNAVAILABLE = False


def accessibility_available() -> bool:
    return not _AX_UNAVAILABLE


def window_bounds(app_name: str) -> dict | None:
    """Front window of an app: {title, left, top, width, height}."""
    global _AX_UNAVAILABLE
    if _AX_UNAVAILABLE:
        return None
    app = resolve_mac_app(app_name)
    script = f'''
    tell application "System Events"
      if not (exists process "{app}") then return ""
      tell process "{app}"
        if (count of windows) is 0 then return ""
        set wname to name of window 1
        set pos to position of window 1
        set sz to size of window 1
        return wname & tab & (item 1 of pos as text) & "," & (item 2 of pos as text) & "," & (item 1 of sz as text) & "," & (item 2 of sz as text)
      end tell
    end tell
    '''
    try:
        result = osascript(script, timeout=2.0)
        raw = (result.stdout or "").strip()
        if result.returncode != 0 and "not allowed" in (result.stderr or "").lower():
            _AX_UNAVAILABLE = True
            print("[Platform] Grant Accessibility to Python/Terminal for window control.")
            return None
        if not raw or "\t" not in raw:
            return None
        title, nums = raw.split("\t", 1)
        parts = [int(p.strip()) for p in nums.split(",") if p.strip()]
        if len(parts) != 4:
            return None
        left, top, width, height = parts
        return {
            "title": title,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right": left + width,
            "bottom": top + height,
        }
    except subprocess.TimeoutExpired:
        _AX_UNAVAILABLE = True
        print("[Platform] System Events timed out. Enable Accessibility for Python/Terminal.")
        return None
    except Exception as exc:
        print(f"[Platform] window_bounds failed: {exc}")
        return None


def set_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=(text or "").encode("utf-8"), check=True, timeout=4)
        return True
    except Exception as exc:
        print(f"[Platform] pbcopy failed: {exc}")
        return False


def get_clipboard() -> str:
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, timeout=4)
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def keystroke_in_app(app_name: str, key: str, command: bool = False, shift: bool = False) -> bool:
    """Send a keystroke to a named Mac app via System Events."""
    if _AX_UNAVAILABLE:
        return False
    app = resolve_mac_app(app_name)
    safe_key = (key or "return").replace('"', '\\"')
    mods = []
    if command:
        mods.append("command down")
    if shift:
        mods.append("shift down")
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    if safe_key.lower() in ("return", "enter"):
        stroke = "keystroke return" + using
    elif safe_key.lower() == "escape":
        stroke = "key code 53" + using
    elif safe_key.lower() == "tab":
        stroke = "keystroke tab" + using
    elif len(safe_key) == 1:
        stroke = f'keystroke "{safe_key}"' + using
    else:
        stroke = f'keystroke "{safe_key}"' + using
    script = f'''
    tell application "{app}" to activate
    delay 0.15
    tell application "System Events"
      if exists process "{app}" then
        tell process "{app}"
          set frontmost to true
          {stroke}
        end tell
        return "ok"
      end if
    end tell
    return "missing"
    '''
    try:
        result = osascript(script, timeout=3)
        return result.returncode == 0 and "ok" in (result.stdout or "")
    except subprocess.TimeoutExpired:
        print("[Platform] keystroke timed out (Accessibility). Falling back to pyautogui.")
        return False
    except Exception as exc:
        print(f"[Platform] keystroke failed: {exc}")
        return False


def kill_named(names: Iterable[str]) -> None:
    for name in names:
        try:
            subprocess.run(["pkill", "-if", name], capture_output=True, check=False)
            subprocess.run(["killall", name], capture_output=True, check=False)
        except Exception:
            pass


def kill_matching_command(needle: str) -> None:
    """Kill processes whose command line contains needle. Never a bare 'chrome'."""
    text = (needle or "").strip()
    if not text or text.lower() in {"chrome", "google chrome", "chromium"}:
        return
    try:
        subprocess.run(["pkill", "-f", text], capture_output=True, check=False)
    except Exception:
        pass


def get_output_volume() -> int:
    result = osascript("output volume of (get volume settings)")
    try:
        return max(0, min(100, int(float(result.stdout.strip()))))
    except Exception as exc:
        raise RuntimeError(f"Could not read macOS volume: {exc}") from exc


def is_output_muted() -> bool:
    result = osascript("output muted of (get volume settings)")
    return result.stdout.strip().lower() == "true"


def set_output_volume(level: int) -> None:
    level = max(0, min(100, int(level)))
    osascript(f"set volume output volume {level}")


def set_output_muted(muted: bool) -> None:
    osascript(f"set volume output muted {'true' if muted else 'false'}")


def system_power(mode: str) -> tuple[bool, str]:
    mode = (mode or "shutdown").lower()
    scripts = {
        "restart": 'tell application "System Events" to restart',
        "sleep": 'tell application "System Events" to sleep',
        "shutdown": 'tell application "System Events" to shut down',
    }
    src = scripts.get(mode, scripts["shutdown"])
    try:
        osascript(src)
        if mode == "restart":
            return True, "SUCCESS: Restarting."
        if mode == "sleep":
            return True, "SUCCESS: Going to sleep."
        return True, "SUCCESS: Shutting down."
    except Exception as exc:
        return False, str(exc)


def notify(title: str, message: str) -> None:
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    try:
        osascript(f'display notification "{safe_message}" with title "{safe_title}"')
    except Exception:
        pass


def which_or(name: str) -> str | None:
    return shutil.which(name)
