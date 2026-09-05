"""
command_processor.py — Core command pipeline (LangGraph + SSE streaming).

Thin entry point: feeds user input into the LangGraph and streams tokens
back to HTTP/WebSocket clients. Pre-graph intercept handles call termination.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator

from brain.friday_graph import run_graph
from brain.memory_store import get_memory_store
from brain.response_builder import get_builder as _get_builder
from config import settings
from services.event_bus import BusEvent, event_bus
from services.runtime_state import (
    current_stream_queue,
    flags,
    log_once,
    new_response_id,
    resolve_thread_id,
    set_state,
    state_lock,
    stop_event,
    SystemState,
)
from services.vision_service import vision_agent
from services.websocket_manager import ws_manager

logger = logging.getLogger("friday.command")

_CALL_END_PHRASES = (
    "end the call",
    "end call",
    "hang up",
    "stop voice",
    "exit voice",
    "close voice",
    "terminate call",
    "stop the call",
)


async def _speak_and_wait(text: str, *, is_smart: bool, response_id: str) -> None:
    """Play TTS to completion before the mic re-opens (companion voice loop)."""
    from executor.watchdog import touch_progress
    from tts.hybrid_tts import speak_hybrid as speak

    if flags.stop_listen_trigger or stop_event.is_set():
        return

    touch_progress()
    flags.tts_spoke_this_turn = False
    try:
        if flags.stop_listen_trigger or stop_event.is_set():
            return
        await set_state(SystemState.SPEAKING)
        await ws_manager.broadcast_json({"type": "tts_started", "text": text})
        # Let the wake-word InputStream notice SPEAKING and release CoreAudio.
        await asyncio.sleep(0.25)
        ok = await speak(text, is_smart=is_smart, response_id=response_id)
        if not ok and len(text.strip()) > 120:
            short = text.strip()[:120].rsplit(" ", 1)[0] + "."
            ok = await speak(short, is_smart=False, response_id=response_id)
        if not ok:
            ok = await speak(
                "Sorry, I had trouble speaking that.",
                is_smart=False,
                response_id=response_id,
            )
        flags.tts_spoke_this_turn = ok
        touch_progress()
    except Exception as exc:
        logger.error("TTS error: %s", exc)
        flags.tts_spoke_this_turn = False
    finally:
        # Companion voice_loop schedules the next listen after TTS fully ends.
        if not flags.continuous_voice_mode or flags.stop_listen_trigger:
            await set_state(SystemState.IDLE)


async def _speak_in_background(text: str, *, is_smart: bool, response_id: str) -> None:
    asyncio.create_task(_speak_and_wait(text, is_smart=is_smart, response_id=response_id))


def _should_skip_request(
    command_text: str,
    request_id: str | None,
    now: float,
    *,
    voice: bool = False,
) -> bool:
    if not command_text.strip():
        return True
    if command_text == flags.last_user_input and now - flags.last_request_time < 3:
        return True
    if request_id and request_id in flags.processed_ids:
        return True
    # Don't drop typed chat just because a voice turn recently finished.
    # Voice loop already waits for the prior turn; don't silently drop here.
    return False


async def _handle_call_termination(*, voice: bool, response_id: str) -> AsyncIterator[str]:
    """Pre-graph intercept for ending continuous voice sessions."""
    from tts.pocket_tts import stop_speech

    flags.continuous_voice_mode = False
    flags.stop_listen_trigger = True
    try:
        stop_speech()
    except Exception:
        pass
    stop_event.set()
    event_bus.emit_nowait(BusEvent("stop"))

    final_response = _get_builder().cancel()
    await ws_manager.broadcast_chat(final_response)

    if voice:
        asyncio.create_task(_speak_in_background(final_response, is_smart=False, response_id=response_id))
    else:
        await set_state(SystemState.IDLE)

    yield f"data: {json.dumps({'text': final_response, 'done': False})}\n\n"
    yield f"data: {json.dumps({'done': True})}\n\n"
    flags.last_response_time = time.time()


async def _stream_graph_response(
    command_text: str,
    request_id: str | None,
    *,
    voice: bool,
    response_id: str,
) -> AsyncIterator[str]:
    """Run LangGraph and stream tokens to SSE consumers and WebSocket clients."""
    from executor.watchdog import touch_progress

    memory_store = get_memory_store()
    asyncio.create_task(asyncio.to_thread(memory_store.store_exchange, "user", command_text))
    vision_agent.current_user_intent = command_text

    thread_id = resolve_thread_id(request_id)
    queue: asyncio.Queue[str] = asyncio.Queue()
    token = current_stream_queue.set(queue)
    streamed_any = False
    final_response = _get_builder().error()
    tts_buffer = None
    from brain.settings import is_muted
    from tts.hybrid_tts import StreamingTtsBuffer

    # For voice turns, full-turn synthesis ensures zero gaps, zero clicks, and pristine speech.
    if not is_muted() and not voice:
        tts_buffer = StreamingTtsBuffer()

    try:
        graph_task = asyncio.create_task(
            run_graph(
                command_text,
                thread_id,
                llm_provider=settings.LLM_PROVIDER or "ollama",
                llm_model=settings.LLM_MODEL,
            )
        )

        while not graph_task.done() or not queue.empty():
            if flags.stop_listen_trigger or stop_event.is_set():
                if not graph_task.done():
                    graph_task.cancel()
                if tts_buffer and tts_buffer.active:
                    tts_buffer.cancel()
                from tts.pocket_tts import stop_speech

                stop_speech()
                break
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.02)
            except asyncio.TimeoutError:
                continue

            streamed_any = True
            if tts_buffer is not None:
                if not tts_buffer.active:
                    tts_buffer.start()
                tts_buffer.feed(chunk)
            await ws_manager.broadcast_json({"type": "response_chunk", "text": chunk})
            yield f"data: {json.dumps({'text': chunk, 'model': settings.LLM_PROVIDER or 'ollama', 'done': False})}\n\n"
            queue.task_done()

        if graph_task.cancelled():
            graph_result = {"response_text": "", "raw_state": {}}
        else:
            try:
                graph_result = await graph_task
            except asyncio.CancelledError:
                graph_result = {"response_text": "", "raw_state": {}}
        final_response = graph_result.get("response_text", _get_builder().error())
        touch_progress()
    finally:
        current_stream_queue.reset(token)

    if final_response:
        asyncio.create_task(
            asyncio.to_thread(memory_store.store_exchange, "assistant", final_response)
        )
        await ws_manager.broadcast_chat(final_response)
        if not streamed_any:
            yield f"data: {json.dumps({'text': final_response, 'model': settings.LLM_PROVIDER or 'ollama', 'done': False})}\n\n"
        raw_state = graph_result.get("raw_state") or {}
        tts_text = (raw_state.get("tts_text") or final_response or "").strip()
        intro_audio = bool(raw_state.get("intro_audio"))

        if intro_audio:
            if tts_buffer and tts_buffer.active:
                tts_buffer.cancel()
            from executor.intro_audio import play_friday_intro

            await set_state(SystemState.SPEAKING)
            await ws_manager.broadcast_json({"type": "tts_started"})
            ok, _ = await asyncio.to_thread(play_friday_intro)
            if not ok:
                fallback = tts_text or final_response
                if fallback:
                    await _speak_and_wait(
                        fallback,
                        is_smart=False,
                        response_id=response_id,
                    )
                elif flags.continuous_voice_mode and not flags.stop_listen_trigger:
                    await set_state(SystemState.IDLE_LISTENING)
                else:
                    await set_state(SystemState.IDLE)
            elif flags.continuous_voice_mode and not flags.stop_listen_trigger:
                await set_state(SystemState.IDLE_LISTENING)
            else:
                await set_state(SystemState.IDLE)
        elif tts_buffer is not None:
            if tts_text and not streamed_any:
                if not tts_buffer.active:
                    tts_buffer.start()
                tts_buffer.feed(tts_text)
            if tts_buffer.active:
                await asyncio.to_thread(tts_buffer.finish)
                await _set_idle_after_stream()
            elif tts_text:
                await _speak_and_wait(
                    tts_text,
                    is_smart=False,
                    response_id=response_id,
                )
        elif tts_text:
            logger.info("Speaking response (%d chars)", len(tts_text))
            await _speak_and_wait(
                tts_text,
                is_smart=False,
                response_id=response_id,
            )
        else:
            if flags.continuous_voice_mode and not flags.stop_listen_trigger:
                await set_state(SystemState.IDLE_LISTENING)
            else:
                await set_state(SystemState.IDLE)

    yield f"data: {json.dumps({'done': True})}\n\n"
    flags.last_response_time = time.time()


async def _set_idle_after_stream() -> None:
    from tts.hybrid_tts import wait_for_streaming_tts_idle

    if flags.stop_listen_trigger or stop_event.is_set():
        await set_state(SystemState.IDLE)
        return
    await wait_for_streaming_tts_idle()
    if flags.continuous_voice_mode and not flags.stop_listen_trigger:
        await set_state(SystemState.IDLE_LISTENING)
    else:
        await set_state(SystemState.IDLE)


async def process_command(
    command_text: str,
    request_id: str | None = None,
    *,
    voice: bool = False,
) -> AsyncIterator[str]:
    """Process a user command and yield Server-Sent Event frames."""
    now = time.time()
    logger.info("Processing command: %r (id=%s)", command_text, request_id)

    # ── Phase 0 duplex guard — drop echo even if voice_loop missed it
    if voice and command_text:
        try:
            from stt.duplex import duplex as _dup  # type: ignore
            drop, reason = _dup.should_drop_transcript(command_text)
            if drop:
                logger.info("Command processor duplex drop (%s): %r", reason, command_text)
                # allow barge-in phrases through
                if not _dup.is_barge_in(command_text):
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return
        except Exception:
            pass
        # also legacy phantom check
        try:
            from stt.filter import is_phantom_transcript as _is_phantom
            from services.runtime_state import flags as _flags
            if _is_phantom(command_text, last_assistant=_flags.last_assistant_response):
                logger.info("Phantom transcript dropped in processor: %r", command_text)
                yield f"data: {json.dumps({'done': True})}\n\n"
                return
        except Exception:
            pass

    if _should_skip_request(command_text, request_id, now, voice=voice):
        yield f"data: {json.dumps({'done': True})}\n\n"
        return

    if not voice:
        wait_deadline = time.time() + 50.0
        while flags.is_processing and time.time() < wait_deadline:
            await asyncio.sleep(0.05)

    with state_lock:
        if _should_skip_request(command_text, request_id, now, voice=voice):
            yield f"data: {json.dumps({'done': True})}\n\n"
            return
        flags.is_processing = True
        flags.voice_turn = voice
        flags.last_request_time = now
        flags.last_user_input = command_text
        if request_id:
            flags.processed_ids.add(request_id)

    try:
        response_id = new_response_id()
        await set_state(SystemState.PROCESSING)
        if not voice:
            from services.companion_state import set_working_task

            preview = command_text.strip()
            if len(preview) > 42:
                preview = preview[:39] + "…"
            await set_working_task("Working", preview or "Processing request")
        from executor.watchdog import touch_progress

        touch_progress()

        cmd_clean = command_text.lower().strip().rstrip("?!., ")
        if any(phrase in cmd_clean for phrase in _CALL_END_PHRASES):
            async for frame in _handle_call_termination(voice=voice, response_id=response_id):
                yield frame
            return

        async for frame in _stream_graph_response(
            command_text,
            request_id,
            voice=voice,
            response_id=response_id,
        ):
            yield frame

    except Exception as exc:
        log_once(f"Process Error: {exc}")
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"
    finally:
        with state_lock:
            flags.is_processing = False
            flags.voice_turn = False
        if voice:
            from tts.hybrid_tts import wait_for_streaming_tts_idle
            from tts.pocket_tts import is_tts_active

            await wait_for_streaming_tts_idle()
            deadline = time.time() + 120.0
            while time.time() < deadline and is_tts_active():
                await asyncio.sleep(0.08)
        from services.companion_state import restore_companion_surface

        await restore_companion_surface()


async def process_command_with_timeout(
    command_text: str,
    request_id: str | None = None,
    *,
    voice: bool = False,
) -> AsyncIterator[str]:
    """Enforce a hard timeout so a stuck graph run cannot lock runtime state."""
    try:
        async with asyncio.timeout(60.0):
            async for item in process_command(command_text, request_id, voice=voice):
                yield item
    except TimeoutError:
        logger.warning("Command processing timed out — resetting state")
        with state_lock:
            flags.is_processing = False
            flags.voice_turn = False
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': 'Process timed out', 'done': True})}\n\n"