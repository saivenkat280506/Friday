"""
services — Background orchestration, runtime state, and shared infrastructure.

Imported by main.py and selectively by executor/brain modules that need
shared runtime objects (event bus, WebSocket manager, stream queue).
"""

from services.event_bus import BusEvent, EventBus, event_bus
from services.runtime_state import (
    RuntimeFlags,
    SystemState,
    current_stream_queue,
    flags,
    log_once,
    reset_processing_state,
    resolve_thread_id,
    set_state,
    state_lock,
    stop_event,
)
from services.websocket_manager import ConnectionManager, ws_manager

__all__ = [
    "BusEvent",
    "ConnectionManager",
    "EventBus",
    "RuntimeFlags",
    "SystemState",
    "current_stream_queue",
    "event_bus",
    "flags",
    "log_once",
    "reset_processing_state",
    "resolve_thread_id",
    "set_state",
    "state_lock",
    "stop_event",
    "ws_manager",
]