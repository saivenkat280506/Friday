"""
websocket_manager.py — WebSocket connection pool and broadcast helpers.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("friday.ws")


class ConnectionManager:
    """Tracks active WebSocket clients and broadcasts JSON payloads."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug("WebSocket connected (%d active)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.debug("WebSocket disconnected (%d active)", len(self.active_connections))

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.debug("Broadcast failed, dropping client: %s", exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_state(self, state: str) -> None:
        await self._broadcast({"state": state})

    async def broadcast_chat(self, text: str, role: str = "assistant") -> None:
        await self._broadcast({"type": "chat", "text": text, "role": role})

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        await self._broadcast(payload)


ws_manager = ConnectionManager()

# Backward-compatible alias used across the codebase.
manager = ws_manager