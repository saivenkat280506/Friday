"""
companion_hotkey.py — Global companion hotkey (default Alt+Space) via low-level hook.

Toggle behavior:
  - Companion closed        → show floating companion card (idle, no mic)
  - Companion open / busy   → dismiss companion and kill linked work
    (listening, thinking, speaking, generating, continuous voice)

Alt+Space is owned by Windows (window system menu); Electron cannot register it.
A Win32 ``RegisterHotKey`` listener handles it safely (no low-level hook crashes).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("friday.companion_hotkey")

_loop: Optional[asyncio.AbstractEventLoop] = None
_win32_hotkey_listeners: list = []
_keyboard_hook_active = False
_last_fire = 0.0
_DEBOUNCE_S = 0.75
_stop_watchdog = threading.Event()
_watchdog_thread: Optional[threading.Thread] = None
_hotkey_watchdog_stop = threading.Event()
_hotkey_watchdog_thread: Optional[threading.Thread] = None

_macos_tap = None
_macos_run_loop = None
_macos_thread: Optional[threading.Thread] = None


def _start_macos_modifier_listener() -> bool:
    """Listen for Control+Option modifier chord on macOS via Quartz CGEventTap."""
    global _macos_thread
    try:
        import Quartz

        def _run():
            global _macos_tap, _macos_run_loop
            last_flags = 0

            def callback(proxy, event_type, event, refcon):
                nonlocal last_flags
                if event_type == Quartz.kCGEventFlagsChanged:
                    flags = Quartz.CGEventGetFlags(event)
                    ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)
                    alt = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                    prev_ctrl = bool(last_flags & Quartz.kCGEventFlagMaskControl)
                    prev_alt = bool(last_flags & Quartz.kCGEventFlagMaskAlternate)
                    last_flags = flags

                    # Leading edge: trigger when both Control and Option become pressed
                    if ctrl and alt and not (prev_ctrl and prev_alt):
                        logger.info("Control+Option detected via macOS Quartz hook")
                        _on_companion_hotkey("macos-control-option")
                return event

            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
                callback,
                None,
            )
            if not tap:
                logger.warning("Quartz CGEventTapCreate returned None (check Accessibility permissions)")
                return

            _macos_tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            _macos_run_loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(_macos_run_loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()

        _macos_thread = threading.Thread(
            target=_run, daemon=True, name="macos_control_option_listener"
        )
        _macos_thread.start()
        return True
    except Exception as exc:
        logger.warning("Failed to start macOS modifier listener: %s", exc)
        return False


def companion_hotkey_label() -> str:
    """Human/Electron-style accelerator (e.g. Control+Option or Alt+Space)."""
    import sys
    default = "Control+Option" if sys.platform == "darwin" else "Alt+Space"
    return os.getenv("COMPANION_HOTKEY", default).strip() or default


def companion_hotkey_fallback_label() -> str:
    """Secondary global hotkey when the primary combo is blocked."""
    return (
        os.getenv("COMPANION_HOTKEY_FALLBACK", "Ctrl+Alt+F").strip() or "Ctrl+Alt+F"
    )


def companion_hotkey_labels() -> list[str]:
    """Primary + fallback accelerators (deduplicated)."""
    labels: list[str] = []
    for label in (companion_hotkey_label(), companion_hotkey_fallback_label()):
        if label and label not in labels:
            labels.append(label)
    return labels


def _keyboard_hotkey_spec() -> str:
    """keyboard library format (e.g. alt+space)."""
    return "+".join(part.strip().lower() for part in companion_hotkey_label().split("+"))


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def keyboard_hook_active() -> bool:
    return _keyboard_hook_active


def stop_f12_agent_processes() -> int:
    """Terminate always-on FridayF12Agent / PowerShell hotkey host processes."""
    if os.name != "nt":
        return 0

    killed = 0
    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is not None:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline = proc.info.get("cmdline") or []
                cmd = " ".join(str(part) for part in cmdline).lower()
                if (
                    name in ("fridayf12agent.exe", "fridayf12intercept.exe")
                    or "f12-hotkey-agent.ps1" in cmd
                ):
                    proc.kill()
                    killed += 1
                    logger.info("Stopped F12 agent pid=%s name=%s", proc.pid, name)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as exc:
                logger.debug("F12 agent stop skipped: %s", exc)

    if killed:
        return killed

    try:
        import subprocess

        for image in ("FridayF12Agent.exe", "FridayF12Intercept.exe"):
            subprocess.run(
                ["taskkill", "/F", "/IM", image],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception as exc:
        logger.debug("taskkill F12 agent failed: %s", exc)

    return killed


def _debounced() -> bool:
    global _last_fire
    now = time.time()
    if now - _last_fire < _DEBOUNCE_S:
        return True
    _last_fire = now
    return False


def _music_only_surface() -> bool:
    """True when only the music card is showing — hotkey should listen, not dismiss."""
    try:
        from executor.local_music_player import get_playback_state
        from services.companion_state import get_companion_task

        if not get_playback_state().get("is_playing"):
            return False
        return get_companion_task().kind in ("music_local", "music_online")
    except Exception:
        return False


def _companion_is_active() -> bool:
    """True when companion is open or any live session is running."""
    from services.runtime_state import flags, get_state, SystemState

    if _music_only_surface():
        return False

    # companion_surface_collapsed tracks idle task UI, not overlay visibility.
    if flags.companion_mode:
        return True
    if flags.is_listening or flags.is_processing or flags.continuous_voice_mode:
        return True
    if flags.force_listen_trigger or flags.pending_ui_listen:
        return True
    return get_state() in (
        SystemState.TRANSCRIBING,
        SystemState.LISTENING,
        SystemState.PROCESSING,
        SystemState.SPEAKING,
    )


async def terminate_background_work(*, stop_music: bool = True) -> dict[str, int]:
    """Stop companion-linked background tasks without wiping memory or checkpoints."""
    counts = {
        "tasks": 0,
        "retries": 0,
        "vision": 0,
    }

    try:
        from executor.task_manager import task_manager

        for tid in list(task_manager.active_tasks.keys()):
            if task_manager.cancel_task(tid):
                counts["tasks"] += 1
    except Exception as exc:
        logger.debug("cancel task_manager tasks: %s", exc)

    try:
        from executor.agent_loop import agent_loop

        counts["retries"] = agent_loop.clear_retry_queue()
    except Exception as exc:
        logger.debug("clear agent retry queue: %s", exc)

    try:
        from services.vision_service import vision_agent

        vision_agent.last_desc = ""
        vision_agent.action_history.clear()
        while not vision_agent.queue.empty():
            try:
                vision_agent.queue.get_nowait()
                vision_agent.queue.task_done()
                counts["vision"] += 1
            except Exception:
                break
    except Exception as exc:
        logger.debug("clear vision queue: %s", exc)

    try:
        from executor.web_agent import request_stop

        request_stop()
    except Exception as exc:
        logger.debug("web_agent stop: %s", exc)

    try:
        from executor.browser_agent_client import get_browser_client, is_browser_agent_available

        if await is_browser_agent_available():
            await get_browser_client().stop_session()
    except Exception as exc:
        logger.debug("browser agent stop: %s", exc)

    if stop_music:
        try:
            from executor.local_music_player import stop as stop_local_music

            await asyncio.to_thread(stop_local_music)
        except Exception as exc:
            logger.debug("stop local music: %s", exc)

    try:
        from executor.intro_audio import stop_friday_intro

        await asyncio.to_thread(stop_friday_intro)
    except Exception as exc:
        logger.debug("stop intro audio: %s", exc)

    return counts


async def dismiss_companion_session(*, source: str = "hotkey") -> dict:
    """
    Close companion and terminate all linked work:
    voice listen, STT, thinking/pipeline, TTS speaking, continuous mode.
    """
    from services.event_bus import BusEvent, event_bus
    from services.runtime_state import (
        flags,
        reset_processing_state,
        set_state,
        state_lock,
        stop_event,
        SystemState,
    )
    from services.websocket_manager import ws_manager
    from tts.pocket_tts import stop_speech

    flags.companion_hotkey_seq += 1
    flags.companion_hotkey_last_action = "close"

    try:
        from services.voice_loop import cancel_active_listen

        cancel_active_listen()
    except Exception as exc:
        logger.debug("cancel_active_listen: %s", exc)

    bg_stopped = await terminate_background_work(stop_music=False)

    # Hard stop all voice / generation paths
    reset_processing_state(keep_companion_mode=False)
    flags.companion_mode = False
    flags.companion_surface_collapsed = True
    flags.continuous_voice_mode = False
    flags.force_listen_trigger = False
    flags.pending_ui_listen = False
    flags.stop_listen_trigger = True
    with state_lock:
        flags.is_listening = False
        flags.is_processing = False
    stop_event.set()
    stop_speech()

    try:
        from tts.hybrid_tts import stop_audio_stream

        await asyncio.to_thread(stop_audio_stream, 2.0)
    except Exception as exc:
        logger.debug("stop_audio_stream: %s", exc)

    try:
        from services.companion_state import set_idle_task

        await set_idle_task()
    except Exception as exc:
        logger.debug("set_idle_task: %s", exc)

    await set_state(SystemState.IDLE)
    event_bus.emit_nowait(BusEvent("stop"))

    payload = {
        "type": "companion_hotkey",
        "seq": flags.companion_hotkey_seq,
        "source": source,
        "action": "close",
    }
    await ws_manager.broadcast_json(payload)
    await ws_manager.broadcast_json({"type": "companion_mode", "active": False})
    await ws_manager.broadcast_json({"type": "companion_dismissed"})
    logger.info(
        "Companion closed via %s — voice stopped, background tasks=%s",
        source,
        bg_stopped,
    )
    return {
        "status": "closed",
        "action": "close",
        "seq": flags.companion_hotkey_seq,
        "listening": False,
        "background_stopped": bg_stopped,
    }


async def open_companion_session(*, source: str = "hotkey") -> dict:
    """Show companion overlay and start listening immediately."""
    from services.companion_state import start_companion_listening
    from services.runtime_state import flags, set_state, SystemState
    from services.websocket_manager import ws_manager

    flags.companion_hotkey_seq += 1
    flags.companion_hotkey_last_action = "open"

    await start_companion_listening()
    await ws_manager.broadcast_json(
        {
            "type": "companion_hotkey",
            "seq": flags.companion_hotkey_seq,
            "source": source,
            "action": "open",
        }
    )
    logger.info("Companion opened via %s — listening active", source)
    return {
        "status": "opened",
        "action": "open",
        "seq": flags.companion_hotkey_seq,
        "listening": True,
    }


async def fire_companion_hotkey(*, source: str = "hotkey") -> dict:
    """
    F12 toggle entry point.
    Open when idle; close + kill all linked tasks when active.
    """
    if _debounced():
        # Ignore bounce; do not flip state twice
        from services.runtime_state import flags

        return {
            "status": "debounced",
            "action": flags.companion_hotkey_last_action,
            "seq": flags.companion_hotkey_seq,
        }

    if _music_only_surface():
        return await _listen_during_music(source=source)

    if _companion_is_active():
        return await dismiss_companion_session(source=source)
    return await open_companion_session(source=source)


async def _listen_during_music(*, source: str = "hotkey") -> dict:
    """Music is playing — hotkey opens mic for commands like volume control."""
    from services.companion_state import set_listening_task
    from services.event_bus import BusEvent, event_bus
    from services.runtime_state import flags, stop_event

    flags.companion_hotkey_seq += 1
    flags.companion_hotkey_last_action = "open"
    flags.companion_mode = True
    flags.continuous_voice_mode = True
    flags.stop_listen_trigger = False
    flags.stt_consecutive_failures = 0
    flags.stt_mic_paused_until = 0.0
    stop_event.clear()

    await set_listening_task()
    event_bus.emit_nowait(BusEvent("wake"))
    logger.info("Companion listen during music via %s", source)
    return {
        "status": "listening",
        "action": "open",
        "seq": flags.companion_hotkey_seq,
        "listening": True,
    }


def _schedule_hotkey(source: str = "keyboard") -> None:
    if _loop is None or not _loop.is_running():
        logger.debug("Companion hotkey ignored — event loop not ready")
        return

    try:
        asyncio.run_coroutine_threadsafe(fire_companion_hotkey(source=source), _loop)
    except Exception as exc:
        logger.warning("Companion hotkey dispatch failed: %s", exc)


def _on_companion_hotkey() -> None:
    _schedule_hotkey("keyboard")


def companion_hotkey_agent_active_flag() -> Path:
    """Temp flag the background agent sets only while it owns Alt+Space."""
    return Path(os.getenv("TEMP", ".")) / "friday-companion-hotkey-agent.active"


def _external_hotkey_agent_enabled() -> bool:
    """True when the background agent is actively listening for Alt+Space."""
    # The agent process can stay alive while idle; only defer when it holds the hook.
    return companion_hotkey_agent_active_flag().is_file()


def _register_keyboard_hook() -> bool:
    global _win32_hotkey_listeners, _keyboard_hook_active
    _unregister_keyboard_hook()
    _keyboard_hook_active = False

    if os.getenv("COMPANION_HOTKEY_USE_KEYBOARD_LIB", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return _register_keyboard_lib_hook()

    try:
        from services.win32_hotkey import start_hotkey_listener

        registered: list[str] = []
        labels = companion_hotkey_labels()
        if _external_hotkey_agent_enabled() and labels:
            # Background agent owns the primary combo; backend keeps fallback only.
            labels = labels[1:]
            logger.info(
                "Skipping %s in backend — external companion hotkey agent active",
                companion_hotkey_label(),
            )
        for label in labels:
            listener, method = start_hotkey_listener(
                label,
                _on_companion_hotkey,
                allow_low_level=True,
            )
            if listener and listener.active:
                _win32_hotkey_listeners.append(listener)
                registered.append(f"{label} ({method})")
                continue
            if listener:
                listener.stop()
            logger.warning("%s hotkey registration unavailable", label)

        if registered:
            _keyboard_hook_active = True
            logger.info(
                "Companion hotkeys active: %s",
                ", ".join(registered),
            )
            return True
    except Exception as exc:
        logger.warning("Win32 hotkey registration failed: %s", exc)

    return _register_keyboard_lib_hook()


def _register_keyboard_lib_hook() -> bool:
    """Legacy fallback — can crash on Alt+Space; opt-in via env only."""
    global _keyboard_hook_active
    spec = _keyboard_hotkey_spec()
    try:
        import keyboard
    except ImportError:
        logger.warning("keyboard package missing — companion hotkey hook disabled")
        return False

    try:
        keyboard.add_hotkey(spec, _on_companion_hotkey, suppress=False)
        _keyboard_hook_active = True
        logger.warning(
            "%s registered via keyboard lib (legacy, suppress=False)",
            companion_hotkey_label(),
        )
        return True
    except Exception as exc:
        logger.warning("%s keyboard lib hook failed: %s", companion_hotkey_label(), exc)
        return False


def _unregister_keyboard_hook() -> None:
    global _win32_hotkey_listeners, _keyboard_hook_active
    _keyboard_hook_active = False
    for listener in _win32_hotkey_listeners:
        try:
            listener.stop()
        except Exception as exc:
            logger.debug("Win32 hotkey stop skipped: %s", exc)
    _win32_hotkey_listeners = []


def _is_myasus_process(name: str) -> bool:
    n = (name or "").lower().strip()
    if n.endswith(".exe"):
        n = n[:-4]
    return n in {"asusmyasus", "myasus"} or n.startswith("asusmyasus")


def _kill_myasus_processes() -> int:
    try:
        import psutil
    except ImportError:
        return 0

    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name") or ""
            if not _is_myasus_process(name):
                continue
            proc.kill()
            killed += 1
            logger.info("Blocked MyASUS process pid=%s name=%s", proc.info.get("pid"), name)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as exc:
            logger.debug("MyASUS kill skipped: %s", exc)
    return killed


def _myasus_watchdog_loop() -> None:
    try:
        import psutil
    except ImportError:
        logger.warning("psutil missing — MyASUS process intercept disabled")
        return

    logger.info("MyASUS process intercept active (F12 OEM key → FRIDAY companion)")
    seen: set[int] = set()

    while not _stop_watchdog.is_set():
        try:
            current: set[int] = set()
            spawned = False
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    pid = proc.info.get("pid")
                    name = proc.info.get("name") or ""
                    if pid is None or not _is_myasus_process(name):
                        continue
                    current.add(pid)
                    if pid not in seen:
                        spawned = True
                        try:
                            proc.kill()
                            logger.info("Intercepted MyASUS launch pid=%s → companion", pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            if spawned:
                _schedule_hotkey("myasus-intercept")

            seen = current
        except Exception as exc:
            logger.debug("MyASUS watchdog tick error: %s", exc)

        _stop_watchdog.wait(0.35)


def _start_myasus_watchdog() -> None:
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _stop_watchdog.clear()
    _watchdog_thread = threading.Thread(
        target=_myasus_watchdog_loop,
        name="MyASUSIntercept",
        daemon=True,
    )
    _watchdog_thread.start()


def _stop_myasus_watchdog() -> None:
    _stop_watchdog.set()


def _hotkey_watchdog_loop() -> None:
    """Re-register backend hotkeys if they drop while the agent is not running."""
    while not _hotkey_watchdog_stop.is_set():
        try:
            if not _external_hotkey_agent_enabled() and not _keyboard_hook_active:
                logger.info("Companion hotkey watchdog — re-registering hooks")
                _register_keyboard_hook()
        except Exception as exc:
            logger.debug("Companion hotkey watchdog tick failed: %s", exc)
        _hotkey_watchdog_stop.wait(2.0)


def _start_hotkey_watchdog() -> None:
    global _hotkey_watchdog_thread
    if _hotkey_watchdog_thread is not None and _hotkey_watchdog_thread.is_alive():
        return
    _hotkey_watchdog_stop.clear()
    _hotkey_watchdog_thread = threading.Thread(
        target=_hotkey_watchdog_loop,
        name="CompanionHotkeyWatchdog",
        daemon=True,
    )
    _hotkey_watchdog_thread.start()


def _stop_hotkey_watchdog() -> None:
    _hotkey_watchdog_stop.set()


async def start_companion_hotkey(loop: asyncio.AbstractEventLoop) -> None:
    bind_loop(loop)
    import sys

    if sys.platform == "darwin":
        if _start_macos_modifier_listener():
            logger.info("Companion hotkey service ready (Control+Option via macOS Quartz)")
        return

    if os.name != "nt":
        logger.info("Companion hotkey service skipped (non-Windows/macOS)")
        return

    # Do not hook or kill ASUS hotkey services — that breaks volume/brightness/Fn keys.
    if _register_keyboard_hook():
        logger.info(
            "Companion hotkey service ready (%s via Win32)",
            companion_hotkey_label(),
        )
    else:
        logger.warning(
            "Companion keyboard hook unavailable — Electron must register %s",
            companion_hotkey_label(),
        )

    _start_hotkey_watchdog()

    if os.getenv("COMPANION_MYASUS_INTERCEPT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        _start_myasus_watchdog()


def refresh_companion_hotkey() -> bool:
    """Re-register global hotkeys (e.g. after the background agent releases Alt+Space)."""
    if os.name != "nt":
        return False
    return _register_keyboard_hook()


def stop_companion_hotkey() -> None:
    global _macos_tap, _macos_run_loop
    if _macos_tap and _macos_run_loop:
        try:
            import Quartz

            Quartz.CGEventTapEnable(_macos_tap, False)
            Quartz.CFRunLoopStop(_macos_run_loop)
        except Exception:
            pass
        _macos_tap = None
        _macos_run_loop = None
    _stop_hotkey_watchdog()
    _stop_myasus_watchdog()
    _unregister_keyboard_hook()
