"""
web_agent.py — Streaming browser agent progress (DOM-based, no vision).
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Callable

from executor.browser_agent import run_browser_agent


async def run_web_agent_streaming(
    task: str,
    broadcast_fn: Callable[[dict], None],
    max_steps: int = 15,
    use_vision: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream browser agent completion as SSE + WebSocket-compatible events."""
    del use_vision  # vision removed — DOM-only control

    payload = {
        "type": "agent_step",
        "step": 1,
        "total": max_steps,
        "action": "browser_agent",
        "result": "running...",
        "status": "running",
        "task": task,
    }
    try:
        await broadcast_fn(payload)
    except Exception:
        pass
    yield f"data: {json.dumps(payload)}\n\n"

    ok, message = await run_browser_agent(task, max_steps=max_steps)
    status = "done" if ok else "error"
    final = {
        "type": "agent_step",
        "step": max_steps,
        "total": max_steps,
        "action": "DONE" if ok else "ERROR",
        "result": message,
        "status": status,
        "task": task,
    }
    try:
        await broadcast_fn(final)
    except Exception:
        pass
    yield f"data: {json.dumps(final)}\n\n"


def request_stop():
    """No-op placeholder for API compatibility."""
    return None


def clear_stop():
    return None


def is_stop_requested() -> bool:
    return False