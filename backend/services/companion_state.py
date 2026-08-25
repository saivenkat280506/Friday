"""
companion_state.py — Task surface state broadcast to the companion overlay.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("friday.companion")

_ws_manager = None


@dataclass
class CompanionTask:
    kind: str = "idle"  # idle | music_local | music_online | task | flash | listening
    title: str = "Ready"
    detail: str = "Tap mic to talk"
    song: str = ""
    platform: str = ""
    is_playing: bool = False
    can_control: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


_task = CompanionTask()


def _music_playing_standby() -> bool:
    """True when the companion should stay on the music card, not reopen the mic."""
    if _task.kind in ("music_local", "music_online"):
        return True
    try:
        from executor.local_music_player import get_playback_state

        return bool(get_playback_state().get("is_playing"))
    except Exception:
        return False


def bind_ws_manager(manager) -> None:
    global _ws_manager
    _ws_manager = manager


def get_companion_task() -> CompanionTask:
    return _task


def _set_task(task: CompanionTask) -> None:
    global _task
    _task = task


async def broadcast_companion_task(task: CompanionTask | None = None) -> None:
    payload = (task or _task).to_payload()
    if _ws_manager is not None:
        await _ws_manager.broadcast_json({"type": "companion_task", **payload})


async def set_idle_task() -> None:
    _set_task(CompanionTask())
    await broadcast_companion_task()


async def show_companion_idle_surface() -> None:
    """Show the floating companion card in idle state without starting voice."""
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    _set_task(CompanionTask())
    await broadcast_companion_task()


async def on_companion_voice_stopped() -> None:
    """Between utterances — keep listening UI and reopen the mic when appropriate."""
    from services.event_bus import BusEvent, event_bus
    from services.runtime_state import flags, get_state, state_lock, SystemState

    if flags.companion_surface_collapsed:
        return
    if not flags.companion_mode:
        await set_idle_task()
        return
    if _music_playing_standby() and not flags.continuous_voice_mode:
        await set_hearing_task()
        return

    flags.continuous_voice_mode = True
    flags.stop_listen_trigger = False
    await set_listening_task()

    with state_lock:
        busy = flags.is_listening or flags.is_processing
        state = get_state()
    if busy or state in (
        SystemState.LISTENING,
        SystemState.PROCESSING,
        SystemState.SPEAKING,
        SystemState.IDLE_LISTENING,
    ):
        return
    event_bus.emit_nowait(BusEvent("wake"))


async def on_runtime_state_change(state) -> None:
    """Keep companion_task aligned with real mic / pipeline state."""
    from services.runtime_state import flags, SystemState

    if flags.companion_surface_collapsed or not flags.companion_mode:
        return
    if _task.kind in ("music_local", "music_online", "flash"):
        return

    if flags.is_listening or state == SystemState.LISTENING:
        await set_listening_task()
        return
    if state == SystemState.TRANSCRIBING:
        await set_listening_task()
        return
    if state == SystemState.PROCESSING:
        await set_working_task("Thinking", "Working on your request…")
        return
    if state == SystemState.SPEAKING:
        return
    if state in (SystemState.IDLE_LISTENING, SystemState.IDLE):
        if _music_playing_standby() and not flags.continuous_voice_mode:
            await set_hearing_task()
            return
        await on_companion_voice_stopped()


async def set_listening_task() -> None:
    """Companion surface while the mic is open for commands."""
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    _set_task(
        CompanionTask(
            kind="listening",
            title="Listening",
            detail="Speak now…",
        )
    )
    await broadcast_companion_task()


async def set_hearing_task() -> None:
    """Wake-word standby while music plays — hear, don't continuous-listen."""
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    _set_task(
        CompanionTask(
            kind="hearing",
            title="Hearing",
            detail="Say “Hey FRIDAY”…",
        )
    )
    await broadcast_companion_task()


async def set_flash_task(
    title: str,
    detail: str,
    *,
    seconds: float = 5.0,
) -> None:
    """Brief wide card for quick answers (time, date, etc.)."""
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    _set_task(
        CompanionTask(
            kind="flash",
            title=title,
            detail=detail,
            extra={"wide": True, "seconds": seconds},
        )
    )
    await broadcast_companion_task()

    async def _collapse() -> None:
        await asyncio.sleep(seconds)
        if _task.kind != "flash":
            return
        if flags.companion_mode and flags.continuous_voice_mode:
            await set_listening_task()
        else:
            await restore_companion_surface()

    asyncio.create_task(_collapse())


async def start_companion_listening() -> None:
    """Enable continuous companion voice and schedule the first listen cycle."""
    from services.event_bus import BusEvent, event_bus
    from services.runtime_state import flags, get_state, state_lock, stop_event, SystemState
    from tts.pocket_tts import stop_speech

    flags.companion_mode = True
    flags.companion_surface_collapsed = False
    flags.continuous_voice_mode = True
    flags.stop_listen_trigger = False
    flags.relisten_hold_until = 0.0
    flags.stt_consecutive_failures = 0
    flags.stt_mic_paused_until = 0.0
    stop_event.clear()
    stop_speech()

    with state_lock:
        flags.is_listening = False
        flags.is_processing = False
        flags.force_listen_trigger = True
        flags.pending_ui_listen = True

    stuck = get_state() in (
        SystemState.TRANSCRIBING,
        SystemState.LISTENING,
        SystemState.PROCESSING,
        SystemState.SPEAKING,
    )
    if stuck:
        from services.runtime_state import set_state

        await set_state(SystemState.IDLE)
        await asyncio.sleep(0.15)

    await set_listening_task()
    if _ws_manager is not None:
        await _ws_manager.broadcast_json({"type": "companion_mode", "active": True})
    event_bus.emit_nowait(BusEvent("wake"))


async def set_music_task(
    *,
    song: str,
    platform: str,
    is_playing: bool = True,
    detail: str = "",
) -> None:
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    if is_playing and flags.companion_mode:
        from services.runtime_state import stop_event
        from services.voice_loop import cancel_active_listen

        flags.continuous_voice_mode = False
        flags.stop_listen_trigger = True
        cancel_active_listen()
        stop_event.set()
    is_local = platform == "local"
    controllable = is_local or platform in ("spotify", "local")
    _set_task(
        CompanionTask(
            kind="music_local" if is_local else "music_online",
            title="Now playing" if is_playing else "Paused",
            detail=detail or (f"Local · {song}" if is_local else platform.replace("_", " ").title()),
            song=song,
            platform=platform,
            is_playing=is_playing,
            can_control=controllable,
        )
    )
    await broadcast_companion_task()


async def set_working_task(title: str, detail: str = "", *, kind: str = "task") -> None:
    from services.runtime_state import flags

    flags.companion_surface_collapsed = False
    _set_task(
        CompanionTask(
            kind=kind,
            title=title,
            detail=detail,
        )
    )
    await broadcast_companion_task()


async def update_music_playback(*, is_playing: bool, song: str | None = None) -> None:
    if _task.kind not in ("music_local", "music_online"):
        return
    if song:
        _task.song = song
    _task.is_playing = is_playing
    _task.title = "Now playing" if is_playing else "Paused"
    await broadcast_companion_task()


async def clear_if_music() -> None:
    if _task.kind in ("music_local", "music_online"):
        await set_idle_task()


async def restore_companion_surface() -> None:
    """After work completes, show music controls or return to listen/hear."""
    from services.runtime_state import flags

    if flags.companion_surface_collapsed:
        return
    try:
        from executor.local_music_player import get_playback_state, sync_playing_flag

        sync_playing_flag()
        playback = get_playback_state()
        if playback.get("has_track"):
            await set_music_task(
                song=playback.get("song", "Local music"),
                platform="local",
                is_playing=playback.get("is_playing", False),
            )
            return
    except Exception as exc:
        logger.debug("restore_companion_surface: %s", exc)

    if flags.companion_mode:
        try:
            from tts.pocket_tts import is_tts_active

            if is_tts_active():
                # voice_loop restores listen UI after TTS finishes — never steal the mic early
                return
        except Exception:
            pass

        if _music_playing_standby():
            return

        if _music_playing_standby() and not flags.continuous_voice_mode:
            await set_hearing_task()
            return
        await on_companion_voice_stopped()
        return

    if _task.kind == "task":
        await set_idle_task()