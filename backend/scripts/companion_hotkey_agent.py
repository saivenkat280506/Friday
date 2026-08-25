"""
Lightweight background Alt+Space listener for FRIDAY companion.

Owns the global Alt+Space hotkey when FRIDAY is stopped so the companion can
cold-start. While FRIDAY is running, forwards presses to POST /companion/f12.

Run at login via scripts/install-companion-hotkey.ps1.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.companion_hotkey import (  # noqa: E402
    companion_hotkey_agent_active_flag,
    companion_hotkey_label,
)
from services.win32_hotkey import start_hotkey_listener  # noqa: E402

BACKEND_URL = os.getenv("FRIDAY_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
SUMMON_SCRIPT = PROJECT_ROOT / "scripts" / "summon-friday-companion.ps1"
LOG_FILE = Path(os.getenv("TEMP", ".")) / "friday-companion-hotkey-agent.log"
ACTIVE_FLAG = companion_hotkey_agent_active_flag()
_BACKEND_POLL_S = 1.5
_DEBOUNCE_S = 0.75
_last_fire = 0.0

_handlers: list[logging.Handler] = [
    logging.FileHandler(LOG_FILE, encoding="utf-8"),
]
if sys.stdout is not None and hasattr(sys.stdout, "write"):
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("friday.companion_hotkey_agent")


def _debounced() -> bool:
    global _last_fire
    now = time.time()
    if now - _last_fire < _DEBOUNCE_S:
        return True
    _last_fire = now
    return False


def _backend_healthy() -> bool:
    try:
        req = urllib.request.Request(f"{BACKEND_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _set_agent_listening(active: bool) -> None:
    try:
        if active:
            ACTIVE_FLAG.write_text(str(os.getpid()), encoding="utf-8")
        elif ACTIVE_FLAG.exists():
            ACTIVE_FLAG.unlink()
    except OSError as exc:
        logger.debug("Active flag update skipped: %s", exc)


def _notify_backend_hotkey_refresh() -> None:
    try:
        req = urllib.request.Request(
            f"{BACKEND_URL}/companion/hotkey/refresh",
            method="POST",
            data=b"",
            headers={"Content-Length": "0"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                logger.info("Backend reclaimed Alt+Space after agent handoff")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Backend hotkey refresh skipped: %s", exc)


def _release_hotkey_listener(listener) -> None:
    if listener:
        listener.stop()
    _set_agent_listening(False)


def _post_companion_toggle() -> dict | None:
    try:
        req = urllib.request.Request(
            f"{BACKEND_URL}/companion/f12",
            method="POST",
            data=b"",
            headers={"Content-Length": "0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {"status": "ok"}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Companion toggle failed: %s", exc)
        return None


def _desktop_running() -> bool:
    try:
        import psutil
    except ImportError:
        return False

    electron_up = False
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == "electron.exe":
                electron_up = True
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not electron_up:
        return False

    try:
        import socket

        with socket.create_connection(("127.0.0.1", 3000), timeout=1.5):
            return True
    except OSError:
        return False


def _start_desktop() -> None:
    frontend = PROJECT_ROOT / "frontend"
    if not frontend.is_dir():
        logger.error("Frontend directory missing: %s", frontend)
        return

    logger.info("Starting FRIDAY desktop (npm run dev:desktop)")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            f"Set-Location -LiteralPath '{frontend}'; npm run dev:desktop",
        ],
        cwd=str(frontend),
        creationflags=creationflags,
    )


def _cold_start() -> None:
    summon = SUMMON_SCRIPT
    program_data = Path(os.getenv("PROGRAMDATA", "C:\\ProgramData")) / "FRIDAY" / "summon-friday-companion.ps1"
    if program_data.is_file():
        summon = program_data
    elif not summon.is_file():
        logger.error("Summon script missing: %s", SUMMON_SCRIPT)
        return

    logger.info("Cold start via %s", summon)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(summon),
        ],
        cwd=str(summon.parent if summon.parent.name == "FRIDAY" else PROJECT_ROOT),
        creationflags=creationflags,
    )


def _post_shutdown() -> bool:
    """Call /app/shutdown to gracefully stop voice, agents, hotkeys, background workers."""
    try:
        req = urllib.request.Request(
            f"{BACKEND_URL}/app/shutdown",
            method="POST",
            data=b"",
            headers={"Content-Length": "0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Graceful shutdown request failed: %s", exc)
        return False


def _kill_port_listeners(port: int) -> int:
    """Force-kill all processes listening on the given port."""
    killed = 0
    try:
        # Use netstat to find PIDs on the port
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pids: set[int] = set()
        for line in result.stdout.splitlines():
            if "LISTENING" in line and f":{port} " in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                    if pid > 0:
                        pids.add(pid)
                except (ValueError, IndexError):
                    continue
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                killed += 1
                logger.info("Killed process PID %d on port %d", pid, port)
            except Exception as exc:
                logger.debug("Failed to kill PID %d: %s", pid, exc)
    except Exception as exc:
        logger.warning("Port listener kill failed for port %d: %s", port, exc)
    return killed


def _kill_electron_processes() -> int:
    """Force-kill all Electron processes."""
    killed = 0
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "electron.exe"],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            killed += 1
    except Exception as exc:
        logger.debug("Electron taskkill failed: %s", exc)
    return killed


def _kill_friday_node_processes() -> int:
    """Kill node.exe processes that belong to the FRIDAY frontend."""
    killed = 0
    try:
        import psutil
    except ImportError:
        return 0

    friday_root_lower = str(PROJECT_ROOT).lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in ("node.exe",):
                continue
            cmdline = " ".join(str(p) for p in (proc.info.get("cmdline") or [])).lower()
            cwd = (proc.info.get("cwd") or "").lower()
            if friday_root_lower in cmdline or friday_root_lower in cwd:
                proc.kill()
                killed += 1
                logger.info("Killed FRIDAY node process PID %d", proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return killed


def _full_shutdown() -> None:
    """
    Completely shut down FRIDAY: graceful API shutdown, then force-kill
    backend (port 8000), frontend (port 3000), Electron, and FRIDAY node processes.
    """
    logger.info("Full FRIDAY shutdown initiated")

    # Step 1: Graceful shutdown via API (stops voice, TTS, agents, hotkeys)
    _post_shutdown()
    time.sleep(1.5)

    # Step 2: Force-kill backend server (port 8000)
    backend_killed = _kill_port_listeners(8000)
    logger.info("Backend processes killed: %d", backend_killed)

    # Step 3: Force-kill frontend dev server (port 3000)
    frontend_killed = _kill_port_listeners(3000)
    logger.info("Frontend processes killed: %d", frontend_killed)

    # Step 4: Force-kill Electron
    electron_killed = _kill_electron_processes()
    logger.info("Electron processes killed: %d", electron_killed)

    # Step 5: Kill any remaining FRIDAY node processes
    node_killed = _kill_friday_node_processes()
    logger.info("FRIDAY node processes killed: %d", node_killed)

    logger.info("Full FRIDAY shutdown complete")


def _on_hotkey() -> None:
    if _debounced():
        return

    label = companion_hotkey_label()
    logger.info("%s pressed", label)

    if _backend_healthy():
        if not _desktop_running():
            logger.info("Backend up but desktop missing — launching UI")
            _start_desktop()
            result = _post_companion_toggle()
            if result:
                logger.info("Companion toggled: %s", result.get("action", result.get("status")))
            return

        # Backend and desktop are both running — toggle companion (Dynamic Island)
        result = _post_companion_toggle()
        if result:
            logger.info("Companion toggled: %s", result.get("action", result.get("status")))
        else:
            logger.warning("Companion toggle failed while FRIDAY is running")
        return

    _cold_start()


def _wait_for_backend_offline(listener) -> None:
    """Yield Alt+Space to the backend while FRIDAY is running."""
    logger.info("FRIDAY backend is running — Alt+Space handled by backend; agent idle")
    _release_hotkey_listener(listener)
    _notify_backend_hotkey_refresh()
    while _backend_healthy():
        time.sleep(_BACKEND_POLL_S)


def main() -> int:
    if os.name != "nt":
        logger.error("Companion hotkey agent requires Windows")
        return 1

    os.environ["FRIDAY_COMPANION_HOTKEY_AGENT"] = "1"
    label = companion_hotkey_label()
    listener = None

    while True:
        if _backend_healthy():
            _wait_for_backend_offline(listener)
            listener = None
            continue

        if listener and listener.active:
            thread = getattr(listener, "_thread", None)
            if thread and thread.is_alive():
                time.sleep(_BACKEND_POLL_S)
                continue
            logger.warning("Companion hotkey listener stopped — re-registering")
            _release_hotkey_listener(listener)
            listener = None

        listener, method = start_hotkey_listener(label, _on_hotkey, allow_low_level=True)
        if listener and listener.active:
            _set_agent_listening(True)
            logger.info("Companion hotkey agent ready (%s via %s)", label, method)
            continue

        _release_hotkey_listener(listener)
        listener = None
        logger.warning(
            "%s unavailable — retrying in 3s (RegisterHotKey and low-level hook failed)",
            label,
        )
        time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())