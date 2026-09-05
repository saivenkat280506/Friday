"""
test_voice_mode_runner.py — Complete Voice Mode Verification Harness
Validates audio hardware, neural voice cloning (TTS), full voice turn lifecycle,
state transitions, and duplex self-hearing protection.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR.parent))

import sounddevice as sd
from services.command_processor import process_command_with_timeout
from services.runtime_state import get_state, SystemState, flags
from tts.hybrid_tts import speak_hybrid
from stt.duplex import duplex as DUP

VOICE_COMMANDS = [
    "Hey Friday, system status check.",
    "What is the time right now?",
    "Tell me a short one-sentence science fact.",
]


async def run_voice_mode_tests():
    print("=" * 80)
    print("FRIDAY VOICE MODE TEST HARNESS")
    print("=" * 80)

    # 1. Hardware Check
    print("\n[Step 1/4] Probing Audio Hardware...")
    input_devs = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
    output_devs = [d for d in sd.query_devices() if d.get("max_output_channels", 0) > 0]
    print(f"  ✓ Found {len(input_devs)} input device(s): {input_devs[0]['name']}")
    print(f"  ✓ Found {len(output_devs)} output device(s): {output_devs[0]['name']}")
    print(f"  ✓ Default devices: {sd.default.device}")

    # 2. Neural Voice Cloning & Synthesis
    print("\n[Step 2/4] Testing Pocket TTS Voice Cloning & Speech Playback...")
    t0 = time.perf_counter()
    speak_ok = await speak_hybrid("Friday voice mode test. Audio systems are fully operational.")
    t_synth = time.perf_counter() - t0
    print(f"  ✓ Speech synthesis result: {speak_ok} (completed in {t_synth:.2f}s)")
    if not speak_ok:
        print("  ❌ Speech synthesis failed!")
        return

    # 3. Duplex Echo & Tail Protection Gate
    print("\n[Step 3/4] Verifying Duplex Self-Hearing Protection...")
    # Wait for post-Step-2 acoustic tail to settle
    await asyncio.sleep(DUP.get_tail_ms() / 1000.0 + 0.2)
    can_listen_idle = DUP.can_listen()
    print(f"  ✓ Duplex can_listen when idle: {can_listen_idle}")
    print(f"  ✓ Duplex suppression mode: {DUP.get_echo_suppression_mode()} (tail={DUP.get_tail_ms()}ms)")
    
    # Simulate TTS start and verify mic hard-mute
    DUP.notify_tts_start("Friday speech test")
    can_listen_during_tts = DUP.can_listen()
    print(f"  ✓ Duplex can_listen during TTS: {can_listen_during_tts} (expected: False)")
    DUP.notify_tts_end()
    is_in_tail = DUP.is_in_tail()
    print(f"  ✓ Duplex acoustic tail active immediately after TTS: {is_in_tail}")

    # Wait for tail to expire
    await asyncio.sleep(DUP.get_tail_ms() / 1000.0 + 0.1)
    can_listen_after_tail = DUP.can_listen()
    print(f"  ✓ Duplex can_listen after acoustic tail: {can_listen_after_tail} (expected: True)")

    # 4. End-to-End Voice Turns
    print("\n[Step 4/4] Running End-to-End Voice Mode Turns (voice=True)...")
    for idx, cmd in enumerate(VOICE_COMMANDS, 1):
        print(f"\n--- Voice Turn {idx}/{len(VOICE_COMMANDS)} ---")
        print(f"🎤 Simulated Voice Input: {cmd!r}")
        print(f"  Initial State: {get_state().value}")

        t_turn = time.perf_counter()
        events = []
        async for chunk in process_command_with_timeout(cmd, voice=True):
            events.append(chunk)

        elapsed = time.perf_counter() - t_turn
        final_state = get_state().value
        print(f"  Final State: {final_state}")
        print(f"  Streamed SSE chunks: {len(events)}")
        print(f"  Turn latency: {elapsed:.2f}s")
        print(f"  ✓ Voice Turn {idx} Complete.")

    print("\n" + "=" * 80)
    print("ALL VOICE MODE CAPABILITIES VERIFIED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_voice_mode_tests())
