import asyncio
import time
import threading
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

_STUCK_TIMEOUT = 50.0  # seconds before considering processing stuck
_last_progress = time.time()
_last_check_stuck = False


def touch_progress():
    global _last_progress
    _last_progress = time.time()


async def _force_reset():
    try:
        from services.runtime_state import flags, set_state, SystemState
        from services.websocket_manager import ws_manager
        from tts.pocket_tts import stop_speech
        stop_speech()
        flags.is_processing = False
        flags.is_listening = False
        flags.continuous_voice_mode = False
        flags.stop_listen_trigger = True
        await set_state(SystemState.IDLE)
        await ws_manager.broadcast_json({"type": "watchdog_reset", "reason": "stuck"})
        print("[Watchdog] Auto-reset triggered.")
    except Exception as e:
        print(f"[Watchdog] Reset error: {e}")


async def _check_health():
    global _last_check_stuck
    try:
        from services.runtime_state import flags
        elapsed = time.time() - _last_progress
        from services.runtime_state import get_state, SystemState

        stuck_state = get_state() in (
            SystemState.TRANSCRIBING,
            SystemState.LISTENING,
        )
        if (
            flags.is_processing
            or flags.is_listening
            or stuck_state
        ) and elapsed > _STUCK_TIMEOUT:
            if not _last_check_stuck:
                print(f"[Watchdog] Stuck detected — processing for {elapsed:.0f}s. Resetting.")
                _last_check_stuck = True
            await _force_reset()
        else:
            _last_check_stuck = False
            if flags.is_processing:
                pass
    except Exception as e:
        print(f"[Watchdog] Health check error: {e}")


def start_watchdog(loop: asyncio.AbstractEventLoop):
    def _run():
        while True:
            try:
                future = asyncio.run_coroutine_threadsafe(_check_health(), loop)
                future.result(timeout=5)
            except Exception:
                pass
            time.sleep(15)

    t = threading.Thread(target=_run, daemon=True, name="friday-watchdog")
    t.start()
    print(f"[Watchdog] Started (timeout={_STUCK_TIMEOUT}s, interval=15s).")
    return t
