"""
inner_loop.py — Phase 3 Heartbeat / Inner Loop
================================================

Friday's background "life" — she can notice things without a new user utterance.

Doc §13 Phase 3 design:
  - 1–3s async loop
  - Gets WorldSnapshot
  - Runs Agenda.get_pending_goals(world)
  - Runs AttentionPolicy.should_speak(ctx)
  - If allowed → generate short response via local model, speak via TTS
  - Never triggers if is_listening or is_processing
  - Rate limit: 1 unsolicited per RATE_LIMIT_MINUTES unless urgent

Inner loop tick flow:
  1. Check presence (SLEEP → skip entirely)
  2. Get world snapshot
  3. Get pending goals from agenda
  4. For each pending goal, check attention policy
  5. If allowed → speak via TTS → mark goal fired → record spoke

The inner loop knows *whether* to speak (attention_policy).
The persona decides *how* (friday_persona).

Startup:
  await start_inner_loop()   — called from services/startup.py
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("friday.inner_loop")

# ── Config ────────────────────────────────────────────────────────────────────

TICK_INTERVAL_S: float = 2.0        # how often the loop ticks (1–3s per spec)
MAX_RESPONSE_CHARS: int = 120       # keep unsolicited responses short

_inner_loop_running = False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_busy() -> bool:
    """True if Friday is currently listening, processing, or speaking."""
    try:
        from services.runtime_state import flags
        if flags.is_listening or flags.is_processing:
            return True
    except Exception:
        pass
    try:
        from tts.pocket_tts import is_tts_active, is_speaking
        if is_tts_active() or is_speaking():
            return True
    except Exception:
        pass
    return False


def _is_shutting_down() -> bool:
    try:
        from services.shutdown import is_shutting_down
        return is_shutting_down()
    except Exception:
        return False


async def _speak_unsolicited(text: str, response_id: str = "") -> None:
    """Speak via TTS and broadcast to overlay. Non-blocking for caller."""
    if not text:
        return
    try:
        from tts.hybrid_tts import speak_hybrid
        from services.runtime_state import SystemState, flags, set_state
        from services.websocket_manager import ws_manager

        flags.last_assistant_response = text
        await ws_manager.broadcast_chat(text)
        await set_state(SystemState.SPEAKING)
        rid = response_id or f"inner_{int(time.time())}"
        await speak_hybrid(text, is_smart=False, response_id=rid)
    except Exception as exc:
        logger.warning("[InnerLoop] speak failed: %s", exc)


# ── Initiative handlers (allowlist from spec §13 Phase 3) ─────────────────────


async def _handle_goal_initiative(goal) -> str:
    """
    Convert a goal into a spoken line.

    For ONCE/MANUAL goals, description IS the line.
    For others, description is used as-is (can be enhanced later with LLM).
    Currently: no LLM call for inner loop — keep it cheap and local.
    """
    desc = goal.description.strip()
    # Keep responses short — the inner loop must not monologue
    if len(desc) > MAX_RESPONSE_CHARS:
        desc = desc[:MAX_RESPONSE_CHARS].rsplit(" ", 1)[0] + "."
    return desc


# ── Main loop ────────────────────────────────────────────────────────────────


async def _inner_loop_tick() -> None:
    """Single tick of the inner loop."""
    # 1. Presence gate — SLEEP means no life
    try:
        from services.presence import presence
        if not presence.can_speak_unsolicited():
            return
    except Exception:
        pass

    # 2. Busy guard — don't interrupt mic or active work
    if _is_busy():
        return

    # 3. World snapshot
    world = None
    try:
        from perception.world import get_world_snapshot
        world = get_world_snapshot()
    except Exception:
        pass

    # 4. Get pending goals
    try:
        from brain.agenda import agenda
        pending = agenda.get_pending_goals(world)
    except Exception as exc:
        logger.debug("[InnerLoop] agenda error: %s", exc)
        return

    if not pending:
        return

    # 5. Attention policy gate
    from brain.attention import attention_policy, SpeakContext

    for goal in pending:
        ctx = SpeakContext(
            urgent=goal.urgent,
            world_app=world.app_display if world else "",
            world_title=world.window_title if world else "",
        )
        if not attention_policy.should_speak(ctx):
            continue

        # 6. Generate spoken line
        line = await _handle_goal_initiative(goal)
        if not line:
            continue

        logger.info("[InnerLoop] initiative: goal=%r → %r", goal.id, line[:60])

        # 7. Mark goal fired BEFORE speaking (avoid double-fire if TTS slow)
        try:
            from brain.agenda import agenda
            agenda.mark_fired(goal.id)
        except Exception:
            pass

        # 8. Record spoke in attention policy
        attention_policy.record_spoke(line)

        # 9. Speak
        await _speak_unsolicited(line, response_id=f"inner_{goal.id}")

        # Only one initiative per tick to avoid flooding
        break


async def inner_loop() -> None:
    """
    Background heartbeat — runs forever until shutdown.
    Spawned as an asyncio task by startup.py.
    """
    global _inner_loop_running
    if _inner_loop_running:
        logger.warning("[InnerLoop] Already running — skipping duplicate start")
        return
    _inner_loop_running = True
    logger.info("[InnerLoop] Started (tick=%.1fs)", TICK_INTERVAL_S)

    try:
        while not _is_shutting_down():
            try:
                await _inner_loop_tick()
            except Exception as exc:
                logger.warning("[InnerLoop] tick error: %s", exc)
            await asyncio.sleep(TICK_INTERVAL_S)
    finally:
        _inner_loop_running = False
        logger.info("[InnerLoop] Stopped")


async def start_inner_loop() -> None:
    """Create the inner loop asyncio task. Called from services/startup.py."""
    asyncio.create_task(inner_loop(), name="inner-loop")
    logger.info("[InnerLoop] Task created")


def is_running() -> bool:
    return _inner_loop_running
