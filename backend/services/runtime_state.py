"""
runtime_state.py — Process-wide mutable state and system-state broadcasting.

Centralises globals previously scattered across main.py so background
services (watchdog, voice loop, command processor) share one source of truth.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.websocket_manager import ConnectionManager

logger = logging.getLogger("friday.runtime")

# Streaming token queue used by LLM clients during graph execution.
current_stream_queue: contextvars.ContextVar = contextvars.ContextVar(
    "current_stream_queue",
    default=None,
)

state_lock = threading.Lock()
stop_event = threading.Event()

_DEFAULT_THREAD_ID = "default"


class SystemState(Enum):
    IDLE = "idle"
    IDLE_LISTENING = "idle_listening"
    LISTENING = "listening"
    PROCESSING = "thinking"
    SPEAKING = "talking"
    TRANSCRIBING = "transcribing"


@dataclass
class RuntimeFlags:
    """Mutable runtime flags shared across async tasks and threads."""

    is_processing: bool = False
    is_listening: bool = False
    mic_muted: bool = False
    force_listen_trigger: bool = False
    pending_ui_listen: bool = False
    stop_listen_trigger: bool = False
    continuous_voice_mode: bool = False
    companion_mode: bool = False
    companion_hotkey_seq: int = 0
    companion_hotkey_last_action: str = "open"  # "open" | "close"
    companion_surface_collapsed: bool = False
    relisten_hold_until: float = 0.0
    stt_consecutive_failures: int = 0
    stt_mic_paused_until: float = 0.0
    voice_turn: bool = False
    tts_spoke_this_turn: bool = False
    last_request_time: float = 0.0
    last_response_time: float = 0.0
    last_user_input: str = ""
    last_assistant_response: str = ""
    last_intent: str = ""
    shutdown_requested: bool = False
    voice_session_active: bool = False
    processed_ids: set[str] = field(default_factory=set)
    session_registry: dict[str, str] = field(default_factory=dict)
    backend_ready: bool = False
    stt_ready: bool = False
    stt_provider: str = "local"
    presence_mode: str = "resident"  # resident | quiet | sleep  (mirrors PresenceMode enum)


flags = RuntimeFlags()
_current_state = SystemState.IDLE
_last_error = ""

# Late-bound reference set by main after ws_manager is created.
_ws_manager: ConnectionManager | None = None


def bind_ws_manager(manager: ConnectionManager) -> None:
    """Attach the WebSocket manager so set_state() can broadcast."""
    global _ws_manager
    _ws_manager = manager


def backend_status_label() -> str:
    """Canonical status for desktop + companion dots: online | starting | offline."""
    # /health is only reachable once HTTP is up — report online so the UI does
    # not stay on STARTING while STT/TTS warm up in the background.
    if flags.backend_ready:
        return "online"
    return "starting"


async def set_state(new_state: SystemState) -> None:
    """Update system state and broadcast to connected WebSocket clients."""
    global _current_state
    if _current_state == new_state:
        return
    _current_state = new_state
    if _ws_manager is not None:
        await _ws_manager.broadcast_state(new_state.value)
    logger.info("State → %s", new_state.name)
    try:
        from services.companion_state import on_runtime_state_change

        await on_runtime_state_change(new_state)
    except Exception as exc:
        logger.debug("Companion surface sync skipped: %s", exc)


def get_state() -> SystemState:
    return _current_state


def log_once(error_msg: str) -> None:
    """Log an error string only when it changes (reduces spam)."""
    global _last_error
    if error_msg != _last_error:
        logger.error("%s", error_msg)
        _last_error = error_msg


def resolve_thread_id(request_id: str | None) -> str:
    if request_id and request_id in flags.session_registry:
        return flags.session_registry[request_id]
    return _DEFAULT_THREAD_ID


def register_session(request_id: str, thread_id: str | None = None) -> str:
    tid = thread_id or _DEFAULT_THREAD_ID
    flags.session_registry[request_id] = tid
    return tid


def unregister_sessions_for_thread(thread_id: str) -> int:
    stale = [k for k, v in flags.session_registry.items() if v == thread_id]
    for key in stale:
        flags.session_registry.pop(key, None)
    return len(stale)


def reset_processing_state(*, keep_companion_mode: bool = True) -> None:
    """Reset voice/processing flags (used by /stop-trigger and watchdog)."""
    flags.force_listen_trigger = False
    flags.pending_ui_listen = False
    flags.is_processing = False
    flags.is_listening = False
    stop_event.set()
    if keep_companion_mode and flags.companion_mode:
        flags.stop_listen_trigger = False
        flags.continuous_voice_mode = True
    else:
        flags.stop_listen_trigger = True
        flags.continuous_voice_mode = False
        if not keep_companion_mode:
            flags.companion_mode = False


def new_response_id() -> str:
    return str(uuid.uuid4())