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

from stt.stt import _resolve_input_device

try:
    from stt.duplex import duplex as _duplex  # type: ignore
except Exception:  # pragma: no cover
    _duplex = None  # type: ignore

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



    input_device = _resolve_input_device()
    stream_kwargs = dict(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=2048,
        latency="high",
        callback=audio_callback,
    )
    if input_device is not None:
        stream_kwargs["device"] = input_device

    def _tts_busy() -> bool:
        try:
            if _duplex is not None:
                return not _duplex.can_listen()
        except Exception:
            pass
        try:
            from tts.pocket_tts import is_tts_active

            return bool(is_tts_active())
        except Exception:
            return False

    def _tail_sleep_if_needed() -> None:
        if _duplex is not None:
            try:
                remain = _duplex.tail_remaining_ms()
                if remain > 0:
                    time_module.sleep(min(remain / 1000.0, 0.6))
            except Exception:
                pass

    # Keep the InputStream closed while Friday is speaking — a live
    # capture stream on macOS glitches and cuts cloned TTS playback.
    # Duplex gate also blocks tail period (acoustic echo).
    while True:
        if stop_check and stop_check():
            return False
        if _tts_busy():
            time_module.sleep(0.12)
            continue
        # Tail guard before opening mic — don't arm during acoustic echo tail
        _tail_sleep_if_needed()
        try:
            with sd.InputStream(**stream_kwargs):
                audio_buffer: list[float] = []
                target_samples = SAMPLE_RATE * CHUNK_DURATION

                while True:
                    if stop_check and stop_check():
                        return False
                    if _tts_busy():
                        return False

                    while len(audio_buffer) < target_samples:
                        if stop_check and stop_check():
                            return False
                        if _tts_busy():
                            return False
                        try:
                            chunk = audio_queue.get(timeout=0.1)
                            audio_buffer.extend(chunk.flatten())
                        except queue.Empty:
                            continue

                    if _tts_busy():
                        return False

                    audio_data = np.array(audio_buffer[:target_samples], dtype=np.float32)
                    try:
                        segments, _ = model.transcribe(audio_data, beam_size=1, language="en")
                        text = "".join(s.text for s in segments).lower().strip()
                    except Exception as e:
                        print(f"[Wake] Transcription error: {e}")
                        audio_buffer = []
                        continue

                    # Duplex echo guard — ignore wake phrase if it's just TTS playback echo
                    if text and _duplex is not None:
                        try:
                            drop, reason = _duplex.should_drop_transcript(text)
                            if drop:
                                print(f"[Wake Duplex] Dropped wake transcript ({reason}): {text!r}")
                                audio_buffer = audio_buffer[-SAMPLE_RATE:]
                                continue
                            # extra: if text is echo but contains barge phrase, treat as barge
                            if _duplex.is_barge_in(text):
                                print(f"[Wake Duplex] Barge-in detected: {text!r}")
                                # allow barge — but half-duplex policy says do not trigger
                                # until TTS is over; we still require TTS to have ended
                                # so we wait for tail then accept
                                _tail_sleep_if_needed()
                        except Exception:
                            pass

                    if text and any(phrase in text for phrase in WAKE_PHRASES):
                        print(f"[Wake] Wake phrase detected in: '{text}'")
                        return True

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
