"""
stop.py — Phase 4 Global Stop Controller
==========================================

"Friday, stop." must cancel hands + speech in under one second.

This module is the single authoritative stop:
  1. Stops TTS immediately (interrupt)
  2. Cancels all running asyncio tasks in task_manager
  3. Disarms the mic (sets flags)
  4. Resets SystemState to IDLE
  5. Broadcasts "stopped" overlay event

Design: stop_all() is fire-and-forget synchronous — it MUST NOT await anything
that could be slow. Use Task.cancel() which just schedules cancellation.
The < 1s guarantee holds because we only call non-blocking APIs.

Usage:
    from executor.stop import stop_all
    stop_all()              # everywhere — from voice_loop, barge-in, inner_loop
    await async_stop_all()  # from async context (awaits TTS stop only)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

logger = logging.getLogger("friday.stop")

_stop_lock = threading.Lock()
_last_stop_at: float = 0.0
_STOP_DEBOUNCE_S: float = 0.3   # prevent double-stop within 300ms


def stop_all(reason: str = "user_command") -> None:
    """
    Hard-stop: cancel tasks, silence TTS, reset mic.
    Thread-safe. Non-blocking. Completes in < 50ms.
    """
    global _last_stop_at
    with _stop_lock:
        now = time.monotonic()
        if now - _last_stop_at < _STOP_DEBOUNCE_S:
            return
        _last_stop_at = now

    logger.info("[Stop] stop_all triggered (%s)", reason)
    _stop_tts()
    _cancel_tasks()
    _reset_flags()
    _broadcast_stopped()


def reset_for_test() -> None:
    global _last_stop_at
    with _stop_lock:
        _last_stop_at = 0.0


async def async_stop_all(reason: str = "user_command") -> None:
    """Async variant — awaits TTS stop coroutine if available."""
    await asyncio.to_thread(stop_all, reason)


# ── Step implementations ───────────────────────────────────────────────────────


def _stop_tts() -> None:
    """Interrupt any active TTS immediately."""
    # 1. pocket_tts stop
    try:
        from tts.pocket_tts import stop_tts
        stop_tts()
    except Exception as exc:
        logger.debug("[Stop] pocket_tts stop error: %s", exc)

    # 2. hybrid_tts stop
    try:
        from tts.hybrid_tts import stop_tts as hybrid_stop
        hybrid_stop()
    except Exception as exc:
        logger.debug("[Stop] hybrid_tts stop error: %s", exc)

    # 3. Kill pygame if it's playing
    try:
        import pygame
        if pygame.mixer.get_init() and pygame.mixer.get_busy():
            pygame.mixer.stop()
    except Exception:
        pass


def _cancel_tasks() -> None:
    """Cancel all tasks tracked by task_manager and asyncio named tasks."""
    # 1. task_manager global instance
    try:
        from executor.task_manager import task_manager
        running = dict(task_manager.active_tasks)
        for tid in running:
            try:
                task_manager.cancel_task(tid)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("[Stop] task_manager cancel error: %s", exc)

    # 2. Cancel asyncio tasks with recognisable names (non-system tasks)
    _SYSTEM_TASK_PREFIXES = (
        "inner-loop", "world-watcher", "voice-loop", "agent-loop",
        "watchdog", "vision-loop", "companion", "tts-warmup",
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            for task in asyncio.all_tasks(loop):
                name = task.get_name() or ""
                if not any(name.startswith(p) for p in _SYSTEM_TASK_PREFIXES):
                    if not task.done():
                        task.cancel()
                        logger.debug("[Stop] Cancelled task: %s", name)
    except RuntimeError:
        pass  # No event loop — fine
    except Exception as exc:
        logger.debug("[Stop] asyncio task cancel error: %s", exc)


def _reset_flags() -> None:
    """Reset mic/processing flags so the system is ready for next command."""
    try:
        from services.runtime_state import flags, SystemState, set_state
        flags.is_processing = False
        flags.is_listening = False
        flags.pending_ui_listen = False
        flags.force_listen_trigger = False
        flags.voice_turn = False
        # Don't await set_state — schedule it
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(set_state(SystemState.IDLE))
                )
        except RuntimeError:
            pass
    except Exception as exc:
        logger.debug("[Stop] flag reset error: %s", exc)


def _broadcast_stopped() -> None:
    """Broadcast stopped event to the Electron overlay."""
    try:
        from services.websocket_manager import ws_manager
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    ws_manager.broadcast({"type": "stopped", "state": "IDLE"})
                )
            )
    except Exception as exc:
        logger.debug("[Stop] broadcast error: %s", exc)
