"""
puppeteer_client.py — Python client for the FRIDAY Puppeteer control plane.
==========================================================================
Starts browser-automation/src/server.mjs on demand and sends JSON commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = os.environ.get("PUPPETEER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PUPPETEER_PORT", "3920"))
BASE = f"http://{HOST}:{PORT}"

# FRIDAY/browser-automation
ROOT = Path(__file__).resolve().parents[2] / "browser-automation"
SERVER_JS = ROOT / "src" / "server.mjs"

_server_proc: Optional[subprocess.Popen] = None


def _health(timeout: float = 1.5) -> bool:
    try:
        with urlopen(f"{BASE}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:
        return False


def ensure_server(start_if_needed: bool = True) -> bool:
    """Return True if the Puppeteer control plane is reachable."""
    global _server_proc
    if _health():
        return True
    if not start_if_needed:
        return False
    if not SERVER_JS.exists():
        raise FileNotFoundError(f"Puppeteer server not found: {SERVER_JS}")

    # Install deps once if needed. Skip Puppeteer's Chrome-for-Testing download;
    # macOS automation uses the installed Google Chrome binary.
    node_modules = ROOT / "node_modules" / "puppeteer" / "package.json"
    if not node_modules.exists():
        print("[Puppeteer] Installing npm dependencies (first run)...")
        install_env = os.environ.copy()
        install_env["PUPPETEER_SKIP_DOWNLOAD"] = "true"
        subprocess.run(
            ["npm", "install"],
            cwd=str(ROOT),
            check=True,
            env=install_env,
        )

    env = os.environ.copy()
    env.setdefault("PUPPETEER_HOST", HOST)
    env.setdefault("PUPPETEER_PORT", str(PORT))
    env["PUPPETEER_SKIP_DOWNLOAD"] = "true"
    from executor.sys_platform import chrome_executable
    chrome_bin = chrome_executable()
    if chrome_bin:
        env.setdefault("CHROME_PATH", chrome_bin)
        env.setdefault("PUPPETEER_EXECUTABLE_PATH", chrome_bin)
    # Use the installed Chrome profile (cookies, logins). Chrome cannot share
    # User Data with another window — we quit Chrome first, then launch it.
    # Opt out with CHROME_USE_REAL_PROFILE=0 to use chrome-profile-data/.
    from executor.sys_platform import chrome_profile_directory, chrome_user_data, quit_google_chrome

    want_clone = str(env.get("CHROME_USE_REAL_PROFILE", "1")).strip().lower() in (
        "0",
        "false",
        "no",
        "clone",
    )
    if want_clone:
        env["CHROME_USE_REAL_PROFILE"] = "0"
        env["CHROME_USER_DATA"] = str(ROOT / "chrome-profile-data")
        env["CHROME_KILL_BEFORE_LAUNCH"] = env.get("CHROME_KILL_BEFORE_LAUNCH") or "1"
        env["CHROME_FALLBACK_ON_LOCK"] = env.get("CHROME_FALLBACK_ON_LOCK") or "1"
    else:
        env["CHROME_USE_REAL_PROFILE"] = "1"
        env["CHROME_USER_DATA"] = env.get("CHROME_USER_DATA") or chrome_user_data()
        env["CHROME_PROFILE_DIRECTORY"] = env.get("CHROME_PROFILE_DIRECTORY") or chrome_profile_directory()
        env["CHROME_KILL_BEFORE_LAUNCH"] = "1"
        env["CHROME_FALLBACK_ON_LOCK"] = env.get("CHROME_FALLBACK_ON_LOCK") or "0"
        print(
            f"[Puppeteer] Using Chrome profile {env['CHROME_PROFILE_DIRECTORY']} "
            f"at {env['CHROME_USER_DATA']}"
        )
        quit_google_chrome()

    env.setdefault("CHROME_PROFILE_DIRECTORY", chrome_profile_directory())
    env.setdefault("CHROME_GOOGLE_EMAIL", "challasaivenkat06@gmail.com")
    env.setdefault("SPOTIFY_GOOGLE_EMAIL", "challasaivenkat06@gmail.com")

    log_path = ROOT / "puppeteer-server.log"
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_f.write(f"CHROME_USE_REAL_PROFILE={env.get('CHROME_USE_REAL_PROFILE')}\n")
    log_f.write(f"CHROME_PROFILE_DIRECTORY={env.get('CHROME_PROFILE_DIRECTORY')}\n")
    log_f.write(f"CHROME_USER_DATA={env.get('CHROME_USER_DATA')}\n")
    log_f.write(f"CHROME_KILL_BEFORE_LAUNCH={env.get('CHROME_KILL_BEFORE_LAUNCH')}\n")
    log_f.flush()

    _server_proc = subprocess.Popen(
        ["node", str(SERVER_JS)],
        cwd=str(ROOT),
        env=env,
        stdout=log_f,
        stderr=log_f,
    )

    # Wait for health
    deadline = time.time() + 45
    while time.time() < deadline:
        if _health(timeout=2.0):
            print(f"[Puppeteer] Control plane ready at {BASE}")
            return True
        if _server_proc.poll() is not None:
            break
        time.sleep(0.4)

    raise RuntimeError(
        f"Puppeteer server failed to start on {BASE}. "
        f"Try: cd browser-automation && npm install && npm start"
    )


def _kill_server() -> None:
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=3)
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
    _server_proc = None
    # Free only FRIDAY-launched Chrome (clone profile / this server). Never kill daily Chrome.
    try:
        from executor.sys_platform import kill_matching_command
        kill_matching_command(str(ROOT / "chrome-profile-data"))
        kill_matching_command(str(ROOT / "user-data"))
        kill_matching_command(str(SERVER_JS))
    except Exception:
        pass


def command(action: str, timeout: float = 120.0, **params: Any) -> dict:
    """Send a command to the Puppeteer service. Auto-starts server if needed.

    Retries once after restarting the control plane if the connection drops
    mid-command (common after long YouTube Music runs).
    """
    # Do not put HTTP timeout into the Puppeteer payload
    params = {k: v for k, v in params.items() if k != "timeout"}
    payload = {"action": action, **params}
    data = json.dumps(payload).encode("utf-8")

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            ensure_server(True)
            req = Request(
                f"{BASE}/command",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception:
                return {"ok": False, "error": body or str(e)}
        except (URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            print(f"[Puppeteer] command '{action}' failed (attempt {attempt+1}): {e}")
            _kill_server()
            time.sleep(1.5)
            continue

    return {"ok": False, "error": f"Puppeteer unreachable after retry: {last_err}"}


def tool_result(result: dict, default_ok_message: str = "Done.") -> tuple[bool, str]:
    """Convert service JSON into (success, spoken message)."""
    if not result:
        return False, "Browser automation returned no result."
    if result.get("ok") is False:
        return False, result.get("message") or result.get("error") or "Browser automation failed."
    msg = result.get("message") or default_ok_message
    # Append useful metrics for scroll tests
    if "avgScrollMs" in result:
        msg += f" Average scroll time {result['avgScrollMs']} ms."
    return True, msg
