"""
event_bus.py — Asynchronous event bus for decoupled command dispatch.

Any component (WebSocket, HTTP endpoint, wake detector) can emit BusEvent
objects; the voice command loop consumes them and dispatches handlers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BusEvent:
    """A single message on the FRIDAY event bus."""

    type: str  # "command", "wake", "stop", "mute"
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """FIFO async queue connecting producers to the voice command loop."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue()

    async def emit(self, event: BusEvent) -> None:
        await self._queue.put(event)

    def emit_nowait(self, event: BusEvent) -> None:
        self._queue.put_nowait(event)

    async def get(self) -> BusEvent:
        return await self._queue.get()


event_bus = EventBus()