"""
startup.py — Background service lifecycle management.

Registers and starts all long-running background tasks during FastAPI
lifespan startup, with structured logging and per-service error isolation.

Service enablement is controlled by ``ServiceConfig`` (env vars or settings).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from services.service_config import ServiceConfig
from services.vision_service import vision_agent
from services.voice_loop import voice_command_loop

logger = logging.getLogger("friday.startup")

ServiceStarter = Callable[[asyncio.AbstractEventLoop], Awaitable[None]]


async def _start_watchdog_heartbeat(loop: asyncio.AbstractEventLoop) -> None:
    from executor.watchdog import start_watchdog, touch_progress

    start_watchdog(loop)

    async def heartbeat() -> None:
        while True:
            touch_progress()
            await asyncio.sleep(30)

    asyncio.create_task(heartbeat(), name="watchdog-heartbeat")


async def _start_agent_loop(_loop: asyncio.AbstractEventLoop) -> None:
    from executor.agent_loop import agent_loop

    asyncio.create_task(agent_loop.run(), name="agent-loop")


async def _start_vision_loop(_loop: asyncio.AbstractEventLoop) -> None:
    asyncio.create_task(vision_agent.run(), name="vision-agent-loop")


async def _start_process_monitor(_loop: asyncio.AbstractEventLoop) -> None:
    from executor.process_monitor import start_process_monitor

    await asyncio.to_thread(start_process_monitor)


async def _start_background_monitor(_loop: asyncio.AbstractEventLoop) -> None:
    from executor.background_monitor import run_background_monitor

    asyncio.create_task(run_background_monitor(), name="background-monitor")


async def _start_voice_loop(_loop: asyncio.AbstractEventLoop) -> None:
    asyncio.create_task(voice_command_loop(), name="voice-command-loop")


async def _start_browser_agent(_loop: asyncio.AbstractEventLoop) -> None:
    from executor.browser_agent_process import start_browser_agent_process

    ok = await start_browser_agent_process()
    if not ok:
        logger.warning("Browser agent sidecar did not start — web automation will use fallbacks")


async def _start_tts_warmup(_loop: asyncio.AbstractEventLoop) -> None:
    from brain.context_manager import is_resource_constrained

    if is_resource_constrained(ram_threshold=88.0):
        logger.info("Pocket TTS warm-up deferred — RAM constrained")
        return

    async def _warm() -> None:
        try:
            from tts.pocket_tts import warm_up_tts

            await asyncio.to_thread(warm_up_tts)
            logger.info("Pocket TTS warm-up complete")
        except Exception as exc:
            logger.warning("Pocket TTS warm-up failed: %s", exc)

    asyncio.create_task(_warm(), name="tts-warmup")


# Ordered list of background services started at application boot.
BACKGROUND_SERVICES: list[tuple[str, ServiceStarter]] = [
    ("watchdog", _start_watchdog_heartbeat),
    ("agent_loop", _start_agent_loop),
    ("vision_loop", _start_vision_loop),
    ("process_monitor", _start_process_monitor),
    ("background_monitor", _start_background_monitor),
    ("voice_loop", _start_voice_loop),
    ("tts_warmup", _start_tts_warmup),
    ("browser_agent", _start_browser_agent),
]


def _load_service_config() -> ServiceConfig:
    """Resolve service toggles from settings file, falling back to env vars."""
    try:
        from brain.settings import get_settings

        return ServiceConfig.from_settings(get_settings())
    except Exception as exc:
        logger.debug("Using env-only service config: %s", exc)
        return ServiceConfig.from_env()


async def start_background_services(
    loop: asyncio.AbstractEventLoop,
    *,
    config: ServiceConfig | None = None,
) -> None:
    """Start registered background services; failures are isolated per service."""
    cfg = config or _load_service_config()
    enabled = [(name, starter) for name, starter in BACKGROUND_SERVICES if cfg.is_enabled(name)]
    skipped = len(BACKGROUND_SERVICES) - len(enabled)

    logger.info(
        "Starting background services (%d enabled, %d skipped)…",
        len(enabled),
        skipped,
    )
    started = 0
    for name, starter in enabled:
        try:
            await starter(loop)
            started += 1
            logger.info("  ✓ %s started", name)
        except Exception as exc:
            logger.error("  ✗ %s failed: %s", name, exc, exc_info=True)

    for name, _ in BACKGROUND_SERVICES:
        if not cfg.is_enabled(name):
            logger.info("  − %s disabled", name)

    logger.info("Background service startup complete (%d/%d)", started, len(enabled))


async def shutdown_services() -> None:
    """Run graceful shutdown hooks (memory consolidation, stop signals)."""
    logger.info("Shutdown initiated")
    try:
        from executor.browser_agent_process import stop_browser_agent_process

        await stop_browser_agent_process()
    except Exception as exc:
        logger.warning("Browser agent shutdown failed: %s", exc)
    try:
        from services.runtime_state import stop_event

        stop_event.set()
        logger.debug("Stop event set for voice/STT workers")
    except Exception as exc:
        logger.warning("Could not set stop event: %s", exc)

    logger.info("Running memory consolidation…")
    try:
        from brain.memory_store import get_memory_store

        store = get_memory_store()
        if store.is_ready:
            await asyncio.to_thread(store.summarize_and_compress_episodes, 1)
            logger.info("Memory consolidation complete")
        else:
            logger.info("Memory store not ready — skipping consolidation")
    except Exception as exc:
        logger.warning("Memory consolidation on shutdown failed: %s", exc)

    logger.info("Shutdown complete")