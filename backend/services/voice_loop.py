"""
voice_loop.py — Event-driven voice command loop.

Consumes BusEvent objects and coordinates wake-word detection, STT,
and command dispatch. WebSocket/HTTP endpoints emit events; this loop
handles them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from datetime import datetime

from brain.response_builder import get_builder as _get_builder
from services.command_processor import process_command_with_timeout
from services.event_bus import BusEvent, event_bus
from services.runtime_state import (
    flags,
    get_state,
    set_state,
    state_lock,
    stop_event,
    SystemState,
)
from services.websocket_manager import ws_manager
from stt.stt import listen_stream
from stt.wake import wait_for_wake_word

logger = logging.getLogger("friday.voice")


def _time_of_day() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


async def _run_wake_detector() -> None:
    """
    Background task: wake-word detection → BusEvent("wake").

    Also honours UI-initiated wake via flags.force_listen_trigger and
    auto-re-triggers listen cycles in continuous voice mode.
    """
    while True:
        if flags.continuous_voice_mode:
            if not flags.is_listening and get_state() == SystemState.IDLE:
                logger.debug("Continuous voice mode — auto-triggering listen")
                event_bus.emit_nowait(BusEvent("wake"))
                await asyncio.sleep(0.5)
                continue
            await asyncio.sleep(0.2)
            continue

        if flags.is_listening or get_state() not in (SystemState.IDLE, SystemState.SPEAKING):
            await asyncio.sleep(0.5)
            continue

        def _barge_in() -> None:
            if get_state() == SystemState.SPEAKING:
                from tts.pocket_tts import stop_speech

                stop_speech()

        def check_trigger() -> bool:
            return (
                flags.force_listen_trigger
                or flags.is_listening
                or get_state() not in (SystemState.IDLE, SystemState.SPEAKING)
            )

        detected = await asyncio.to_thread(
            wait_for_wake_word,
            stop_check=check_trigger,
            barge_in_callback=_barge_in,
        )

        if detected and not (
            flags.is_listening or get_state() not in (SystemState.IDLE, SystemState.SPEAKING)
        ):
            event_bus.emit_nowait(BusEvent("wake"))

        flags.force_listen_trigger = False
        await asyncio.sleep(0.1)


async def _handle_listen_cycle() -> None:
    """Full listen → transcribe → process cycle triggered by a 'wake' event."""
    from tts.pocket_tts import stop_speech

    stop_speech()
    flags.stop_listen_trigger = False
    stop_event.clear()

    with state_lock:
        if flags.is_listening:
            return
        if get_state() not in (SystemState.IDLE, SystemState.SPEAKING):
            return

    await ws_manager.broadcast_json({"type": "wake_word_detected"})
    await set_state(SystemState.LISTENING)
    with state_lock:
        flags.is_listening = True

    await asyncio.sleep(0.2)
    loop = asyncio.get_event_loop()

    def partial_cb(partial_text: str, countdown: int | None = None) -> None:
        asyncio.run_coroutine_threadsafe(set_state(SystemState.TRANSCRIBING), loop)
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast_json({
                "type": "partial_transcript",
                "text": partial_text,
                "countdown": countdown,
            }),
            loop,
        )

    def get_text() -> str:
        return listen_stream(partial_cb=partial_cb, stop_event=stop_event)

    command_text = await asyncio.to_thread(get_text)
    now = time.time()

    with state_lock:
        if not command_text or flags.is_processing or (now - flags.last_request_time < 1.5):
            if command_text:
                logger.debug("Debounce skip: %r", command_text)
            flags.is_listening = False
            await ws_manager.broadcast_json({"type": "transcript_clear"})
            await set_state(SystemState.IDLE)
            return
        flags.last_request_time = now

    logger.info("USER: %s", command_text)
    await set_state(SystemState.PROCESSING)
    await ws_manager.broadcast_json({"type": "user_message", "text": command_text})

    async for _ in process_command_with_timeout(command_text, voice=True):
        pass

    with state_lock:
        flags.is_listening = False
    await set_state(
        SystemState.IDLE_LISTENING if not flags.stop_listen_trigger else SystemState.IDLE
    )


async def _handle_text_command(text: str, req_id: str | None = None, *, voice: bool = False) -> None:
    async for _ in process_command_with_timeout(text, req_id, voice=voice):
        pass


async def _handle_stop_event() -> None:
    from tts.pocket_tts import stop_speech

    flags.force_listen_trigger = False
    flags.stop_listen_trigger = True
    flags.continuous_voice_mode = False
    stop_speech()
    stop_event.set()
    await set_state(SystemState.IDLE)


async def _handle_mute_event(muted: bool) -> None:
    from brain.settings import set_mute_state

    set_mute_state(muted)


async def voice_command_loop() -> None:
    """
    Event-driven main loop.

    Consumes BusEvent objects and dispatches to the appropriate handler.
    """
    logger.info("Event-driven voice loop started")
    await asyncio.sleep(0.5)

    tod = _time_of_day()
    await ws_manager.broadcast_chat(_get_builder().greeting(tod))
    await ws_manager.broadcast_json({"type": "system_ready"})

    asyncio.create_task(_run_wake_detector())

    try:
        while True:
            event = await event_bus.get()

            if event.type == "wake":
                asyncio.create_task(_handle_listen_cycle())
            elif event.type == "command":
                text = event.data.get("text", "")
                req_id = event.data.get("id")
                voice = event.data.get("voice", False)
                if text:
                    asyncio.create_task(_handle_text_command(text, req_id, voice=voice))
            elif event.type == "stop":
                await _handle_stop_event()
            elif event.type == "mute":
                await _handle_mute_event(event.data.get("muted", True))

            await asyncio.sleep(0)
    except Exception as exc:
        logger.error("Voice loop error: %s", exc)
        traceback.print_exc()
        await set_state(SystemState.IDLE)