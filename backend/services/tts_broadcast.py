"""
tts_broadcast.py — Push live TTS playback state to WebSocket clients (companion UI).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("friday.tts_broadcast")

_loop: Optional[asyncio.AbstractEventLoop] = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def notify_tts_active(active: bool) -> None:
    """Thread-safe: companion mic capsule reflects real speaker output."""
    if _loop is None or not _loop.is_running():
        return

    async def _emit() -> None:
        from services.runtime_state import SystemState, flags, get_state, set_state
        from services.websocket_manager import ws_manager

        await ws_manager.broadcast_json({"type": "tts_active", "active": active})
        if active:
            await set_state(SystemState.SPEAKING)
            await ws_manager.broadcast_json({"type": "tts_started"})
            return

        if get_state() == SystemState.SPEAKING:
            if flags.is_processing or flags.is_listening:
                await ws_manager.broadcast_json({"type": "tts_stopped"})
                return
            if not flags.tts_spoke_this_turn:
                await set_state(SystemState.IDLE)
            elif flags.continuous_voice_mode and not flags.stop_listen_trigger:
                await set_state(SystemState.IDLE_LISTENING)
            else:
                await set_state(SystemState.IDLE)
        await ws_manager.broadcast_json({"type": "tts_stopped"})

    try:
        asyncio.run_coroutine_threadsafe(_emit(), _loop)
    except Exception as exc:
        logger.debug("tts broadcast skipped: %s", exc)