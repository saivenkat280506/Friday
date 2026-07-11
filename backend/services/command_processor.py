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


async def _speak_in_background(text: str, *, is_smart: bool, response_id: str) -> None:
    from executor.watchdog import touch_progress
    from tts.hybrid_tts import speak_hybrid as speak
    from tts.pocket_tts import stop_speech

    touch_progress()
    try:
        stop_speech()
        await set_state(SystemState.SPEAKING)
        await speak(text, is_smart=is_smart, response_id=response_id)
        touch_progress()
    except Exception as exc:
        logger.error("Background TTS error: %s", exc)
    finally:
        await set_state(SystemState.IDLE)


def _should_skip_request(command_text: str, request_id: str | None, now: float) -> bool:
    if not command_text.strip():
        return True
    if command_text == flags.last_user_input and now - flags.last_request_time < 3:
        return True
    if request_id and request_id in flags.processed_ids:
        return True
    if now - flags.last_response_time < 2:
        return True
    if flags.is_processing:
        return True
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
    from tts.hybrid_tts import StreamingTtsBuffer

    memory_store = get_memory_store()
    asyncio.create_task(asyncio.to_thread(memory_store.store_exchange, "user", command_text))
    vision_agent.current_user_intent = command_text

    thread_id = resolve_thread_id(request_id)
    queue: asyncio.Queue[str] = asyncio.Queue()
    token = current_stream_queue.set(queue)
    streamed_any = False
    final_response = _get_builder().error()
    tts_buffer: StreamingTtsBuffer | None = None
    _tts_speaking_set = False

    def _on_first_sentence():
        nonlocal _tts_speaking_set
        if not _tts_speaking_set:
            _tts_speaking_set = True
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(set_state(SystemState.SPEAKING), loop)
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast_json({"type": "tts_started"}), loop
            )

    if voice:
        from brain.settings import is_muted

        if not is_muted():
            tts_buffer = StreamingTtsBuffer(on_first_sentence=_on_first_sentence)
            await asyncio.to_thread(tts_buffer.start)

    try:
        graph_task = asyncio.create_task(
            run_graph(
                command_text,
                thread_id,
                llm_provider="groq",
                llm_model=settings.LLM_MODEL,
            )
        )

        while not graph_task.done() or not queue.empty():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.02)
            except asyncio.TimeoutError:
                continue

            streamed_any = True
            if tts_buffer and tts_buffer.active:
                tts_buffer.feed(chunk)
            await ws_manager.broadcast_json({"type": "response_chunk", "text": chunk})
            yield f"data: {json.dumps({'text': chunk, 'model': 'groq', 'done': False})}\n\n"
            queue.task_done()

        graph_result = await graph_task
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
            yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
        if voice:
            raw_state = graph_result.get("raw_state") or {}
            tts_text = (raw_state.get("tts_text") or final_response or "").strip()
            if tts_buffer and tts_buffer.active:
                if tts_text and not streamed_any:
                    tts_buffer.feed(tts_text)
                await asyncio.to_thread(tts_buffer.finish)
                asyncio.create_task(_set_idle_after_stream())
            elif tts_text:
                logger.info("Speaking response (%d chars)", len(tts_text))
                asyncio.create_task(
                    _speak_in_background(
                        tts_text,
                        is_smart=False,
                        response_id=response_id,
                    )
                )
            else:
                await set_state(SystemState.IDLE)
        else:
            await set_state(SystemState.IDLE)

    yield f"data: {json.dumps({'done': True})}\n\n"
    flags.last_response_time = time.time()


async def _set_idle_after_stream() -> None:
    from tts.hybrid_tts import wait_for_streaming_tts_idle

    await wait_for_streaming_tts_idle()
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

    if _should_skip_request(command_text, request_id, now):
        yield f"data: {json.dumps({'done': True})}\n\n"
        return

    with state_lock:
        if _should_skip_request(command_text, request_id, now):
            yield f"data: {json.dumps({'done': True})}\n\n"
            return
        flags.is_processing = True
        flags.last_request_time = now
        flags.last_user_input = command_text
        if request_id:
            flags.processed_ids.add(request_id)

    try:
        response_id = new_response_id()
        await set_state(SystemState.PROCESSING)
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
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': 'Process timed out', 'done': True})}\n\n"