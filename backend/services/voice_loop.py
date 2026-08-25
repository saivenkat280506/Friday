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
from stt.stt import last_listen_had_speech, last_listen_mic_ok, listen_stream
from stt.wake import wait_for_wake_word

logger = logging.getLogger("friday.voice")

_listen_lock = asyncio.Lock()
_active_listen_task: asyncio.Task | None = None


def _ready_listen_states() -> tuple[SystemState, ...]:
    """States that may start a new mic capture cycle."""
    base = (SystemState.IDLE, SystemState.IDLE_LISTENING, SystemState.SPEAKING)
    if flags.continuous_voice_mode:
        return base
    return (SystemState.IDLE, SystemState.IDLE_LISTENING, SystemState.SPEAKING)


CONTINUOUS_RELISTEN_GAP_S = 0.35
STT_READY_WAIT_S = 45.0
STT_MIC_FAILURE_BACKOFF_BASE_S = 2.0
STT_MIC_FAILURE_BACKOFF_MAX_S = 60.0
STT_MIC_FAILURE_PAUSE_AFTER = 5


def _music_playing() -> bool:
    try:
        from executor.local_music_player import get_playback_state

        playback = get_playback_state()
        return bool(playback.get("is_playing"))
    except Exception:
        return False


def _apply_mic_failure_backoff() -> bool:
    """Backoff continuous relisten after mic failures. Returns True if voice paused."""
    flags.stt_consecutive_failures += 1
    backoff = min(
        STT_MIC_FAILURE_BACKOFF_MAX_S,
        STT_MIC_FAILURE_BACKOFF_BASE_S * (2 ** (flags.stt_consecutive_failures - 1)),
    )
    flags.relisten_hold_until = time.time() + backoff
    if flags.stt_consecutive_failures >= STT_MIC_FAILURE_PAUSE_AFTER:
        flags.continuous_voice_mode = False
        flags.stt_mic_paused_until = time.time() + STT_MIC_FAILURE_BACKOFF_MAX_S
        return True
    return False


async def _set_post_listen_state(
    *,
    had_transcript: bool = True,
    mic_ok: bool = True,
) -> None:
    """Return to listen, hear (music), or idle after a voice cycle."""
    if had_transcript:
        flags.stt_consecutive_failures = 0
        flags.relisten_hold_until = time.time() + CONTINUOUS_RELISTEN_GAP_S
    elif not mic_ok:
        if _apply_mic_failure_backoff():
            await ws_manager.broadcast_chat(
                "Microphone unavailable — voice paused. "
                "Check your mic settings and press Alt+Space to try again."
            )
            await set_state(SystemState.IDLE)
            return
    else:
        flags.relisten_hold_until = time.time() + CONTINUOUS_RELISTEN_GAP_S
    if _music_playing() and flags.companion_mode:
        from executor.local_music_player import get_playback_state
        from services.companion_state import get_companion_task, set_music_task

        playback = get_playback_state()
        task = get_companion_task()
        if task.kind not in ("music_local", "music_online"):
            await set_music_task(
                song=playback.get("song", "Local music"),
                platform="local",
                is_playing=True,
                detail=f"Local · {playback.get('song', 'music')}",
            )
        await set_state(SystemState.IDLE)
        return
    if flags.continuous_voice_mode and not flags.stop_listen_trigger:
        from services.companion_state import set_listening_task
        from services.event_bus import BusEvent, event_bus

        if not flags.tts_spoke_this_turn:
            flags.relisten_hold_until = time.time() + 0.5
        await set_listening_task()
        await set_state(SystemState.IDLE_LISTENING)
        event_bus.emit_nowait(BusEvent("wake"))
    else:
        await set_state(SystemState.IDLE)


def _clean_transcript(text: str) -> str:
    """Drop empty STT noise such as bare quotes, punctuation-only, or whitespace."""
    quote_chars = "\"'`\u201c\u201d\u2018\u2019"
    cleaned = text.strip().strip(quote_chars)
    if not cleaned:
        return ""
    if all(ch in quote_chars or ch.isspace() for ch in cleaned):
        return ""
    junk = {".", ",", "!", "?", "-", "…", "thank you", "thanks for watching"}
    if cleaned.lower() in junk or len(cleaned) < 2:
        return ""
    return cleaned


async def _await_stt_ready(timeout: float = STT_READY_WAIT_S) -> bool:
    """Block until on-device STT models are warm — avoids dead first listen."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if flags.stt_ready:
            return True
        await asyncio.sleep(0.15)
    return bool(flags.stt_ready)


async def _await_tts_complete(timeout: float = 120.0) -> None:
    """Wait until Pocket/streaming TTS finishes so the mic is not re-opened too early."""
    from tts.pocket_tts import is_speaking, is_streaming

    deadline = time.time() + timeout
    while time.time() < deadline:
        speaking = (
            get_state() == SystemState.SPEAKING
            or is_speaking()
            or is_streaming()
        )
        if not speaking:
            await asyncio.sleep(0.15)
            if (
                get_state() != SystemState.SPEAKING
                and not is_speaking()
                and not is_streaming()
            ):
                return
        await asyncio.sleep(0.1)


def _time_of_day() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _mic_reserved() -> bool:
    """True when the wake-word detector must release the microphone."""
    try:
        from tts.pocket_tts import is_tts_active

        tts_busy = is_tts_active()
    except Exception:
        tts_busy = False
    return (
        flags.force_listen_trigger
        or flags.pending_ui_listen
        or flags.is_listening
        or flags.is_processing
        or tts_busy
        or get_state()
        in (
            SystemState.LISTENING,
            SystemState.TRANSCRIBING,
            SystemState.PROCESSING,
            SystemState.SPEAKING,
        )
    )


async def _run_wake_detector() -> None:
    """
    Background task: wake-word detection → BusEvent("wake").

    Also honours UI-initiated wake via flags.force_listen_trigger and
    auto-re-triggers listen cycles in continuous voice mode.
    """
    while True:
        if flags.companion_mode and _music_playing():
            from executor.local_music_player import get_playback_state
            from services.companion_state import get_companion_task, set_music_task

            task = get_companion_task()
            if task.kind not in ("music_local", "music_online"):
                playback = get_playback_state()
                await set_music_task(
                    song=playback.get("song", "Local music"),
                    platform="local",
                    is_playing=True,
                    detail=f"Local · {playback.get('song', 'music')}",
                )
            if _mic_reserved():
                await asyncio.sleep(0.1)
                continue

            def _barge_in_music() -> None:
                if get_state() == SystemState.SPEAKING:
                    from tts.pocket_tts import stop_speech

                    stop_speech()

            detected = await asyncio.to_thread(
                wait_for_wake_word,
                stop_check=lambda: _mic_reserved(),
                barge_in_callback=_barge_in_music,
            )
            if detected and not _mic_reserved():
                event_bus.emit_nowait(BusEvent("wake"))
            await asyncio.sleep(0.1)
            continue

        if flags.companion_mode and not flags.continuous_voice_mode:
            await asyncio.sleep(0.2)
            continue

        if flags.continuous_voice_mode:
            if time.time() < flags.stt_mic_paused_until:
                await asyncio.sleep(0.5)
                continue
            if time.time() < flags.relisten_hold_until:
                await asyncio.sleep(0.2)
                continue
            try:
                from tts.pocket_tts import is_tts_active

                if is_tts_active():
                    await asyncio.sleep(0.15)
                    continue
            except Exception:
                pass
            ready_states = (SystemState.IDLE_LISTENING,)
            if (
                not flags.stop_listen_trigger
                and not flags.is_listening
                and not flags.is_processing
                and get_state() in ready_states
            ):
                logger.debug("Continuous voice — scheduling next listen")
                event_bus.emit_nowait(BusEvent("wake"))
                await asyncio.sleep(CONTINUOUS_RELISTEN_GAP_S)
                continue
            await asyncio.sleep(0.2)
            continue

        if _mic_reserved():
            await asyncio.sleep(0.1)
            continue

        def _barge_in() -> None:
            if get_state() == SystemState.SPEAKING:
                from tts.pocket_tts import stop_speech

                stop_speech()

        def check_trigger() -> bool:
            return _mic_reserved()

        detected = await asyncio.to_thread(
            wait_for_wake_word,
            stop_check=check_trigger,
            barge_in_callback=_barge_in,
        )

        if flags.force_listen_trigger or flags.pending_ui_listen:
            # UI-triggered listen is in flight — keep mic released until cycle starts.
            await asyncio.sleep(0.2)
            continue

        if detected and not _mic_reserved():
            event_bus.emit_nowait(BusEvent("wake"))

        await asyncio.sleep(0.1)


async def _handle_listen_cycle() -> None:
    """Full listen → transcribe → process cycle triggered by a 'wake' event."""
    from tts.pocket_tts import stop_speech

    async with _listen_lock:
        from tts.pocket_tts import is_tts_active

        if not is_tts_active():
            stop_speech()
        flags.stop_listen_trigger = False
        stop_event.clear()

        with state_lock:
            if flags.is_listening:
                logger.debug("Listen cycle skipped — mic already active")
                flags.pending_ui_listen = False
                flags.force_listen_trigger = False
                return
            retry_listen = False
            if get_state() not in _ready_listen_states():
                logger.debug(
                    "Listen cycle skipped — state=%s",
                    get_state().value,
                )
                retry_listen = (
                    flags.companion_mode
                    and not flags.is_processing
                    and not flags.stop_listen_trigger
                )
                if retry_listen:
                    flags.force_listen_trigger = True
                else:
                    flags.pending_ui_listen = False
                    flags.force_listen_trigger = False
                if retry_listen:

                    async def _retry_wake() -> None:
                        await asyncio.sleep(0.35)
                        if not flags.stop_listen_trigger and flags.companion_mode:
                            event_bus.emit_nowait(BusEvent("wake"))

                    asyncio.create_task(_retry_wake())
                return
            flags.is_listening = True
            flags.pending_ui_listen = False
            flags.force_listen_trigger = False

        try:
            await _await_tts_complete()
            if not await _await_stt_ready():
                logger.warning("STT not ready — skipping listen cycle")
                await ws_manager.broadcast_chat(
                    "Voice models are still loading — try again in a few seconds."
                )
                await _set_post_listen_state(had_transcript=False, mic_ok=True)
                return
            await ws_manager.broadcast_json({"type": "transcript_clear"})
            await ws_manager.broadcast_json({"type": "wake_word_detected"})
            await set_state(SystemState.LISTENING)

            from stt.audio_prep import release_mic_blockers

            release_mic_blockers()
            await asyncio.sleep(0.35)
            loop = asyncio.get_event_loop()

            def partial_cb(
                partial_text: str,
                countdown: int | None = None,
                *,
                phase: str | None = None,
            ) -> None:
                payload = {
                    "type": "partial_transcript",
                    "text": partial_text,
                    "countdown": countdown,
                }
                if phase:
                    payload["phase"] = phase
                elif not partial_text and countdown is None:
                    payload["phase"] = "hearing"
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast_json(payload),
                    loop,
                )

            partial_cb("", phase="open")

            async def _run_stt() -> str:
                # Stay in LISTENING — broadcasting TRANSCRIBING was wiping overlay text.

                def get_text() -> str:
                    return listen_stream(partial_cb=partial_cb, stop_event=stop_event)

                return await asyncio.to_thread(get_text)

            try:
                raw_text = await asyncio.wait_for(_run_stt(), timeout=50.0)
            except TimeoutError:
                logger.warning("STT listen timed out — releasing mic")
                stop_event.set()
                raw_text = ""
            command_text = _clean_transcript(raw_text)
            mic_ok = last_listen_mic_ok()
            had_speech = last_listen_had_speech()

            if flags.is_processing:
                wait_deadline = time.time() + 12.0
                while time.time() < wait_deadline and flags.is_processing:
                    await asyncio.sleep(0.12)

            skip_command = False
            with state_lock:
                if not command_text:
                    skip_command = True
                elif flags.is_processing:
                    logger.warning(
                        "Voice command dropped — still processing previous turn: %r",
                        command_text,
                    )
                    await ws_manager.broadcast_chat(
                        "Still working on your last request — give me a moment, then try again."
                    )
                    skip_command = True
                elif (
                    command_text == flags.last_user_input
                    and time.time() - flags.last_request_time < 0.6
                ):
                    logger.debug("Duplicate voice command skipped: %r", command_text)
                    skip_command = True

            if skip_command:
                if command_text:
                    logger.debug("Voice command not processed: %r", command_text)
                elif mic_ok and had_speech:
                    await ws_manager.broadcast_chat(
                        "I heard you but couldn't make out the words — try speaking a bit closer to the mic."
                    )
                elif mic_ok:
                    await ws_manager.broadcast_chat(
                        "I didn't catch that — wait for Listening, then speak clearly."
                    )
                else:
                    logger.warning(
                        "Mic capture failed (%d consecutive) — backing off relisten",
                        flags.stt_consecutive_failures + 1,
                    )
                await ws_manager.broadcast_json({"type": "transcript_clear"})
                await _set_post_listen_state(
                    had_transcript=bool(command_text),
                    mic_ok=mic_ok,
                )
                return

            logger.info("USER: %s", command_text)
            await set_state(SystemState.PROCESSING)
            await ws_manager.broadcast_json({"type": "user_message", "text": command_text})

            async for _ in process_command_with_timeout(command_text, voice=True):
                pass

            await _await_tts_complete()
            await _set_post_listen_state()
        except asyncio.CancelledError:
            logger.warning("Listen cycle cancelled")
            await ws_manager.broadcast_json({"type": "transcript_clear"})
            await _set_post_listen_state(had_transcript=False, mic_ok=True)
            raise
        except Exception as exc:
            logger.error("Listen cycle error: %s", exc)
            traceback.print_exc()
            await ws_manager.broadcast_json({"type": "transcript_clear"})
            await set_state(SystemState.IDLE)
        finally:
            with state_lock:
                flags.is_listening = False


def cancel_active_listen(*, keep_continuous_mode: bool = False) -> None:
    """Hard-stop mic capture and in-flight graph work."""
    global _active_listen_task
    from tts.pocket_tts import stop_speech

    flags.force_listen_trigger = False
    flags.pending_ui_listen = False
    if keep_continuous_mode:
        flags.stop_listen_trigger = False
    else:
        flags.stop_listen_trigger = True
        flags.continuous_voice_mode = False
    with state_lock:
        flags.is_listening = False
        flags.is_processing = False
    stop_event.set()
    stop_speech()
    if _active_listen_task and not _active_listen_task.done():
        _active_listen_task.cancel()


def _schedule_listen_cycle() -> None:
    """Ensure only one listen cycle runs at a time."""
    global _active_listen_task

    if _active_listen_task and not _active_listen_task.done():
        if time.time() - getattr(_schedule_listen_cycle, "_last_start", 0.0) < 45.0:
            logger.debug("Listen cycle already scheduled")
            return
        logger.warning("Listen cycle stuck — cancelling and restarting")
        _active_listen_task.cancel()
        with state_lock:
            flags.is_listening = False
        stop_event.set()

    _schedule_listen_cycle._last_start = time.time()
    _active_listen_task = asyncio.create_task(_handle_listen_cycle())


async def _handle_text_command(text: str, req_id: str | None = None, *, voice: bool = False) -> None:
    async for _ in process_command_with_timeout(text, req_id, voice=voice):
        pass


async def _handle_stop_event() -> None:
    cancel_active_listen()
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
    from services.tts_broadcast import bind_loop

    bind_loop(asyncio.get_event_loop())
    await asyncio.sleep(0.5)

    tod = _time_of_day()
    if not flags.companion_mode:
        await ws_manager.broadcast_chat(_get_builder().greeting(tod))
    await ws_manager.broadcast_json({"type": "system_ready"})
    from services.companion_state import broadcast_companion_task

    await broadcast_companion_task()

    asyncio.create_task(_run_wake_detector())

    try:
        while True:
            event = await event_bus.get()

            if event.type == "wake":
                _schedule_listen_cycle()
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