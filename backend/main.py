"""
main.py — FRIDAY Unified Core (entry point)
============================================
Single entry point for ``python main.py``. Wires FastAPI, background
services, and the event-driven voice loop.

Structural layout
-----------------
- ``services/``  — runtime state, event bus, command processor, voice loop,
                   startup/shutdown, service toggles
- ``api/routes`` — HTTP + WebSocket endpoints
- ``main.py``    — application factory and backward-compatible re-exports
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Path & environment ────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
sys.path.insert(0, _BACKEND_DIR)
sys.path.insert(0, _PROJECT_ROOT)

from paths import ensure_data_dirs  # noqa: E402

ensure_data_dirs()

if getattr(sys, "frozen", False):
    _env_path = os.path.join(os.path.dirname(sys.executable), ".env")
else:
    _env_path = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_env_path)

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("friday.main")

# ── Service layer (import after path setup) ─────────────────────────────────
from api.routes import register_routes  # noqa: E402
from services.command_processor import process_command, process_command_with_timeout  # noqa: E402
from services.event_bus import BusEvent, EventBus, event_bus  # noqa: E402
from services.runtime_state import (  # noqa: E402
    bind_ws_manager,
    current_stream_queue,
    flags,
    log_once,
    set_state,
    state_lock,
    stop_event,
    SystemState,
)
from services.startup import shutdown_services, start_background_services  # noqa: E402
from services.vision_service import vision_agent  # noqa: E402
from services.voice_loop import voice_command_loop  # noqa: E402
from services.websocket_manager import manager, ws_manager  # noqa: E402

bind_ws_manager(ws_manager)


async def _boot_background_services(loop: asyncio.AbstractEventLoop) -> None:
    """Start heavy background services without blocking HTTP/WebSocket availability."""
    try:
        await start_background_services(loop)
        logger.info("Background services online")
    except Exception as exc:
        logger.error("Background service startup failed: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot background services on startup; consolidate memory on shutdown."""
    loop = asyncio.get_event_loop()
    logger.info("FRIDAY backend starting — HTTP routes available immediately")
    asyncio.create_task(_boot_background_services(loop), name="friday-boot")
    yield
    try:
        await shutdown_services()
    except Exception as exc:
        logger.error("Shutdown error: %s", exc, exc_info=True)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(title="FRIDAY Backend", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routes(application)
    return application


app = create_app()


# ── Backward-compatible re-exports ──────────────────────────────────────────
# Legacy modules may still ``from main import manager`` etc.
# Mutable state lives on ``services.runtime_state.flags``.

__all__ = [
    "app",
    "BusEvent",
    "EventBus",
    "create_app",
    "current_stream_queue",
    "event_bus",
    "flags",
    "log_once",
    "manager",
    "process_command",
    "process_command_with_timeout",
    "set_state",
    "state_lock",
    "stop_event",
    "SystemState",
    "vision_agent",
    "voice_command_loop",
    "ws_manager",
]


if __name__ == "__main__":
    import socket

    import uvicorn

    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return sock.connect_ex(("127.0.0.1", port)) == 0

    if _port_in_use(8000):
        logger.error(
            "Port 8000 is already in use. Stop the existing FRIDAY backend first: "
            "npm run stop:desktop"
        )
        raise SystemExit(1)

    logger.info("Launching uvicorn on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)