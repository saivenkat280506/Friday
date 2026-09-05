"""
continuous_listener.py — Friday Continuous Voice Listener
=========================================================
Stays awake during conversations, handles multi-turn dialogue,
uses Friday-style personality, supports interruptions, and
routes tasks to the autonomous OS agent.
"""

import threading
import re
import time
import random
from stt.stt_continuous import STT
from executor.os_agent import run_os_agent
from tts.pocket_tts import speak
from brain.agent_graph import status_updater

# ── Global state ──────────────────────────────────────────────────────────────
is_free_hands_active = False
stt_instance = None
is_awake = False
is_processing = False  # Prevents overlapping commands
_conversation_timeout = None  # Timer to go back to sleep after inactivity

WAKE_WORDS = ["wake up friday", "friday", "hey friday", "yo friday", "ok friday"]

# How long (seconds) Friday stays awake waiting for the next command
CONVERSATION_TIMEOUT = 30

# ── Friday Personality Lines ──────────────────────────────────────────────────

WAKE_RESPONSES = [
    "At your service, Boss.",
    "Online, Boss.",
    "Ready, Boss.",
    "Here, Boss.",
    "Standing by, Boss.",
]

THINKING_RESPONSES = [
    "On it, Boss.",
    "Working on it, Boss.",
    "Right away, Boss.",
    "Processing, Boss.",
]

DONE_RESPONSES = [
    "Done, Boss.",
    "Complete, Boss.",
    "All set, Boss.",
    "Finished, Boss.",
]

ERROR_RESPONSES = [
    "Task failed, Boss.",
    "Couldn't complete that, Boss.",
    "Error, Boss.",
]

SLEEP_RESPONSES = [
    "Going dark, Boss.",
    "Standing down, Boss.",
    "On standby, Boss.",
]

IDLE_RESPONSES = [
    "Still here, Boss.",
    "Standing by, Boss.",
    "Ready, Boss.",
]


def _reset_conversation_timer():
    """Reset the timer that puts Friday back to sleep after inactivity."""
    global _conversation_timeout
    
    if _conversation_timeout:
        _conversation_timeout.cancel()
    
    def _go_to_sleep():
        global is_awake
        if is_awake and not is_processing:
            is_awake = False
            response = random.choice(SLEEP_RESPONSES)
            print(f"[Listener] {response}")
            speak(response)
            if status_updater:
                status_updater("idle")
    
    _conversation_timeout = threading.Timer(CONVERSATION_TIMEOUT, _go_to_sleep)
    _conversation_timeout.daemon = True
    _conversation_timeout.start()


def _process_text_in_background(text: str):
    global is_awake, is_processing
    
    # Don't process if already executing another command
    if is_processing:
        return
    
    text_lower = text.lower().strip()
    
    # ── Filter out noise / very short utterances ──
    if len(text_lower) < 2:
        return
    
    # ── Common false positives from Whisper ──
    noise_phrases = [
        "thank you", "thanks", "you", "the", "bye", "okay",
        "uh", "um", "hmm", "ah", "oh", "huh", 
        "thank you for watching", "subscribe",
        "thanks for watching",
    ]
    if text_lower in noise_phrases:
        return
    
    # ── Check for wake word ──
    if not is_awake:
        found_wake = False
        for w in WAKE_WORDS:
            if w in text_lower:
                found_wake = True
                break
        if not found_wake:
            return  # Ignore if not awake and no wake word
            
        print("[Listener] ⚡ Woke up!")
        is_awake = True
        
        if status_updater:
            status_updater("listening")
            
        # Strip wake words from the text to get the actual command
        for w in WAKE_WORDS:
            text_lower = text_lower.replace(w, "").strip()
        text = text_lower
        
        # If just a wake word with no command, greet and wait
        if not text.strip():
            response = random.choice(WAKE_RESPONSES)
            print(f"[Listener] {response}")
            speak(response)
            if status_updater:
                status_updater("listening")
            _reset_conversation_timer()
            return
    
    # ── "Go to sleep" / "That's all" commands ──
    sleep_phrases = ["go to sleep", "that's all", "nevermind", "never mind", 
                     "stop listening", "goodbye", "good night", "shut down listener"]
    for phrase in sleep_phrases:
        if phrase in text_lower:
            is_awake = False
            response = random.choice(SLEEP_RESPONSES)
            print(f"[Listener] {response}")
            speak(response)
            if status_updater:
                status_updater("idle")
            return
    
    if not text.strip():
        if status_updater:
            status_updater("listening")
        return
    
    # ── Execute the command ──
    is_processing = True
    print(f"[Listener] 🎯 Command: {text}")
    
    if status_updater:
        status_updater("thinking")
    
    # Quick acknowledgement before starting 
    ack = random.choice(THINKING_RESPONSES)
    speak(ack)
    
    try:
        result = run_os_agent(text)
        
        if status_updater:
            status_updater("talking")
        
        # Parse the result and respond naturally
        if "SUCCESS" in result:
            # Extract the summary after "SUCCESS:"
            summary = result.split("SUCCESS:")[-1].strip() if "SUCCESS:" in result else result
            # Pick a natural completion response + the summary
            done_line = random.choice(DONE_RESPONSES)
            if summary and len(summary) > 5:
                full_response = f"{summary}. {done_line}"
            else:
                full_response = done_line
            speak(full_response)
        elif "ERROR" in result or "FALLBACK" in result:
            error_line = random.choice(ERROR_RESPONSES)
            speak(error_line)
        else:
            speak(result)
            
    except Exception as e:
        print(f"[Listener] Error: {e}")
        if status_updater:
            status_updater("talking")
        speak(random.choice(ERROR_RESPONSES))
    finally:
        is_processing = False
        
        # Stay awake for follow-up commands
        if is_awake:
            _reset_conversation_timer()
            if status_updater:
                status_updater("listening")
        else:
            if status_updater:
                status_updater("idle")


def on_text(text: str):
    threading.Thread(target=_process_text_in_background, args=(text,), daemon=True).start()


def toggle_free_hands(enable: bool):
    global is_free_hands_active, stt_instance, is_awake
    
    if enable and not is_free_hands_active:
        print("[Listener] Enabling Free Hands Mode (Continuous STT)")
        is_free_hands_active = True
        is_awake = False
        
        # Initialize STT with a 6-second silence duration to wait out pauses
        stt_instance = STT(
            on_text=on_text,
            on_recording_start=lambda: status_updater("listening") if status_updater else None,
            on_recording_stop=lambda: status_updater("thinking") if status_updater else None,
            config={
                "silence_ms": 6000, 
                "min_speech_ms": 300,
            }
        )
        stt_instance.start(block=False)
        speak("Free hands mode active, Boss.")
        
    elif not enable and is_free_hands_active:
        print("[Listener] Disabling Free Hands Mode")
        is_free_hands_active = False
        is_awake = False
        if stt_instance:
            stt_instance.stop()
            stt_instance = None
        speak("Free hands mode off, Boss.")
