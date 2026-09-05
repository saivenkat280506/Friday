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

_launch_announced = False

from services.service_config import ServiceConfig
from services.vision_service import vision_agent
from services.voice_loop import voice_command_loop

logger = logging.getLogger("friday.startup")

ServiceStarter = Callable[[asyncio.AbstractEventLoop], Awaitable[None]]


async def _wait_for_desktop(timeout: float = 25.0) -> None:
    from services.shutdown import is_shutting_down
    from services.websocket_manager import ws_manager

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if is_shutting_down():
            return
        if ws_manager.active_connections:
            await asyncio.sleep(0.8)
            return
        await asyncio.sleep(0.25)


async def _announce_online() -> None:
    """Speak the time-of-day launch line once, in F.R.I.D.A.Y.'s voice."""
    global _launch_announced
    if _launch_announced:
        return
    from services.shutdown import is_shutting_down
    if is_shutting_down():
        return
    from brain.personality import launch_greeting_line
    from brain.settings import is_muted
    from services.runtime_state import SystemState, flags, set_state
    from services.websocket_manager import ws_manager
    from tts.hybrid_tts import speak_hybrid

    line = launch_greeting_line()
    flags.last_assistant_response = line
    await _wait_for_desktop()
    if is_shutting_down():
        return
    _launch_announced = True
    logger.info("Launch greeting: %s", line)
    await ws_manager.broadcast_chat(line)
    await ws_manager.broadcast_json({"type": "system_ready"})
    if not is_muted():
        await set_state(SystemState.SPEAKING)
        await asyncio.sleep(0.25)
        await speak_hybrid(line, is_smart=False, response_id="launch_greeting")
    if is_shutting_down():
        return
    await _enable_voice_mode_after_greeting()


async def _enable_voice_mode_after_greeting() -> None:
    """Keep the mic in continuous voice mode after the launch line."""
    from services.event_bus import BusEvent, event_bus
    from services.runtime_state import SystemState, flags, set_state, stop_event
    from services.websocket_manager import ws_manager

    flags.continuous_voice_mode = True
    flags.voice_session_active = True
    flags.stop_listen_trigger = False
    flags.force_listen_trigger = False
    flags.pending_ui_listen = False
    flags.relisten_hold_until = 0.0
    flags.stt_consecutive_failures = 0
    stop_event.clear()
    await set_state(SystemState.IDLE_LISTENING)
    await ws_manager.broadcast_json({"type": "voice_mode", "active": True})
    event_bus.emit_nowait(BusEvent("wake"))
    logger.info("Voice mode ON — continuous listening after greeting")


async def _start_watchdog_heartbeat(loop: asyncio.AbstractEventLoop) -> None:
    from executor.watchdog import start_watchdog

    start_watchdog(loop)


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


async def _start_companion_hotkey(loop: asyncio.AbstractEventLoop) -> None:
    from services.companion_hotkey import start_companion_hotkey

    await start_companion_hotkey(loop)


async def _start_tts_warmup(_loop: asyncio.AbstractEventLoop) -> None:
    from brain.context_manager import is_resource_constrained

    if is_resource_constrained(ram_threshold=88.0):
        logger.info("Pocket TTS warm-up deferred — RAM constrained")
        asyncio.create_task(_announce_online(), name="launch-greeting")
        return

    async def _warm() -> None:
        try:
            from tts.pocket_tts import warm_up_tts

            await asyncio.to_thread(warm_up_tts)
            logger.info("Pocket TTS warm-up complete")
        except Exception as exc:
            logger.warning("Pocket TTS warm-up failed: %s", exc)
        try:
            await _announce_online()
        except Exception as exc:
            logger.warning("Launch greeting failed: %s", exc)
        try:
            from stt.stt import warm_stt_models

            await asyncio.to_thread(warm_stt_models)
            from services.runtime_state import flags

            flags.stt_ready = True
            logger.info("STT model warm-up complete")
            try:
                from services.websocket_manager import ws_manager

                await ws_manager.broadcast_json(
                    {
                        "type": "backend_status",
                        "backend_status": "online",
                        "ready": flags.backend_ready,
                        "stt_ready": True,
                    }
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("STT warm-up failed: %s", exc)
            from services.runtime_state import flags

            flags.stt_ready = True

    asyncio.create_task(_warm(), name="tts-warmup")


# Ordered list of background services started at application boot.
async def _start_ollama_warmup(loop: asyncio.AbstractEventLoop) -> None:
    """Keep the local model resident so the first user turn is not a cold load."""

    async def _warm() -> None:
        try:
            from config import settings, use_ollama
            if not use_ollama():
                return
            from brain.ollama_client import ollama_complete

            await ollama_complete("ok", max_tokens=4, stream=False)
            logger.info("Ollama warm-up complete (%s)", settings.OLLAMA_MODEL)
        except Exception as exc:
            logger.warning("Ollama warm-up failed: %s", exc)

    asyncio.create_task(_warm(), name="ollama-warmup")


async def _start_world_watcher(_loop: asyncio.AbstractEventLoop) -> None:
    """Phase 2 — cheap desktop perception (1s frontmost app/title poll)."""
    try:
        from perception.world import start_world_watcher
        await start_world_watcher()
    except Exception as exc:
        logger.warning("World watcher failed to start: %s", exc)


async def _start_inner_loop(_loop: asyncio.AbstractEventLoop) -> None:
    """Phase 3 — heartbeat inner loop (agenda + attention policy)."""
    try:
        from services.inner_loop import start_inner_loop
        await start_inner_loop()
    except Exception as exc:
        logger.warning("Inner loop failed to start: %s", exc)


BACKGROUND_SERVICES: list[tuple[str, ServiceStarter]] = [
    ("watchdog", _start_watchdog_heartbeat),
    ("agent_loop", _start_agent_loop),
    ("vision_loop", _start_vision_loop),
    ("process_monitor", _start_process_monitor),
    ("background_monitor", _start_background_monitor),
    ("voice_loop", _start_voice_loop),
    ("companion_hotkey", _start_companion_hotkey),
    ("ollama_warmup", _start_ollama_warmup),
    ("tts_warmup", _start_tts_warmup),
    ("browser_agent", _start_browser_agent),
    ("world_watcher", _start_world_watcher),   # Phase 2 — desktop perception
    ("inner_loop", _start_inner_loop),          # Phase 3 — heartbeat
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
    _SERVICE_TIMEOUT_S = 45.0
    for name, starter in enabled:
        try:
            await asyncio.wait_for(starter(loop), timeout=_SERVICE_TIMEOUT_S)
            started += 1
            logger.info("  ✓ %s started", name)
        except asyncio.TimeoutError:
            logger.error("  ✗ %s timed out after %.0fs — continuing boot", name, _SERVICE_TIMEOUT_S)
        except Exception as exc:
            logger.error("  ✗ %s failed: %s", name, exc, exc_info=True)

    for name, _ in BACKGROUND_SERVICES:
        if not cfg.is_enabled(name):
            logger.info("  − %s disabled", name)

    logger.info("Background service startup complete (%d/%d)", started, len(enabled))
    try:
        from services.runtime_state import flags
        from stt.stt import _use_groq_stt

        flags.stt_provider = "groq" if _use_groq_stt() else "local"
        flags.backend_ready = True
        if flags.stt_provider == "groq":
            flags.stt_ready = True
        logger.info("Background services online — STT provider=%s", flags.stt_provider)
        try:
            from services.websocket_manager import ws_manager

            await ws_manager.broadcast_json(
                {
                    "type": "backend_status",
                    "backend_status": "online",
                    "ready": True,
                    "stt_ready": flags.stt_ready,
                }
            )
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Backend ready flag not set: %s", exc)


async def shutdown_services() -> None:
    """Run graceful shutdown hooks (memory consolidation, stop signals)."""
    logger.info("Shutdown initiated")
    try:
        from services.runtime_state import flags

        flags.backend_ready = False
        flags.stt_ready = False
    except Exception:
        pass
    try:
        from executor.browser_agent_process import stop_browser_agent_process

        await stop_browser_agent_process()
    except Exception as exc:
        logger.warning("Browser agent shutdown failed: %s", exc)
    try:
        from services.companion_hotkey import stop_companion_hotkey

        stop_companion_hotkey()
    except Exception as exc:
        logger.warning("Companion hotkey shutdown failed: %s", exc)
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