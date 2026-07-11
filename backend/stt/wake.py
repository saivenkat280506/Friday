"""
wake.py — Wake Word Detection System
=====================================
Listens for the keyword phrases and triggers STT.
Supported phrases: "friday", "hey friday", "wake up friday", "wake friday"
"""

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
import queue
import time as time_module

# Configuration
MODEL_SIZE = "tiny.en"
SAMPLE_RATE = 16000
CHUNK_DURATION = 2      # Seconds of audio per analysis window
COMPUTE_TYPE = "int8"
# Phrases that trigger wake word detection
WAKE_PHRASES = ["friday", "hey friday", "wake up friday", "wake friday"]

# Global model — loaded once, reused
_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
    return _model


def wait_for_wake_word(stop_check=None, barge_in_callback=None) -> bool:
    """
    Continuously listens for wake phrases. 
    If stop_check() returns True, it breaks early (e.g., triggered by UI).
    If barge_in_callback is provided, it's called when voice activity is detected.
    """
    model = get_model()
    audio_queue: queue.Queue = queue.Queue(maxsize=30)

    def audio_callback(indata: np.ndarray, frames: int, cb_time, status):
        if audio_queue.full():
            try: audio_queue.get_nowait()
            except queue.Empty: pass
        audio_queue.put_nowait(indata.copy())



    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=2048,
            latency="high",
            callback=audio_callback,
        ):
            audio_buffer: list[float] = []
            target_samples = SAMPLE_RATE * CHUNK_DURATION

            while True:
                if stop_check and stop_check():
                    return True  # Triggered by UI!

                # --- Fill buffer up to one CHUNK_DURATION of audio ---
                # --- Fill buffer up to one CHUNK_DURATION of audio ---
                while len(audio_buffer) < target_samples:
                    try:
                        chunk = audio_queue.get(timeout=0.2)
                        audio_buffer.extend(chunk.flatten())
                    except queue.Empty:
                        continue

                # --- Transcribe ---
                audio_data = np.array(audio_buffer[:target_samples], dtype=np.float32)
                try:
                    segments, _ = model.transcribe(audio_data, beam_size=1, language="en")
                    text = "".join(s.text for s in segments).lower().strip()
                except Exception as e:
                    print(f"[Wake] Transcription error: {e}")
                    audio_buffer = []
                    continue

                # --- Check for wake phrases ---
                if text and any(phrase in text for phrase in WAKE_PHRASES):
                    print(f"[Wake] Wake phrase detected in: '{text}'")
                    return True

                # Slide window: keep last 1 second for continuity
                audio_buffer = audio_buffer[-SAMPLE_RATE:]

    except Exception as e:
        print(f"[Wake] Stream error: {e}")
        return False


if __name__ == "__main__":
    try:
        while True:
            if wait_for_wake_word():
                print("[System] Wake phrase triggered — ready for command.")
    except KeyboardInterrupt:
        print("\n[System] Shutdown.")
