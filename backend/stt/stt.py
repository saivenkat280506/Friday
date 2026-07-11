"""
stt.py — 3-Layer VAD & Worker STT Pipeline
===================================================
A highly optimized, async STT engine using PyAudio, WebRTC VAD, 
and Faster-Whisper running in a dedicated worker thread to prevent mic dropping.

Layers:
1. PyAudio Stream: Captures 30ms frames blindly.
2. WebRTC VAD: Gates the frame buffering.
3. Queue & Worker: Pushes partial updates off-thread and handles transcription.
"""

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
import queue
import time
import math
import threading
import os
from config import settings

# ── Configuration ────────────────────────────────────────────────────────────
MODEL_SIZE    = settings.STT_MODEL
SAMPLE_RATE   = 16000
FRAME_DURATION= 30       # ms 
FRAME_SIZE    = int(SAMPLE_RATE * FRAME_DURATION / 1000) * 2 # 960 bytes (16-bit PCM)
COMPUTE_TYPE  = settings.STT_COMPUTE_TYPE
VAD_MODE      = 3        # 0-3 (1=mild, 3=aggressive)

# Timers
SILENCE_LIMIT_FRAMES    = 50   # 50 * 30ms = 1.5s of silence ends the command
INITIAL_TIMEOUT_FRAMES  = 150  # 150 * 30ms = 4.5s timeout if user never speaks
PARTIAL_INTERVAL        = 0.5   # Emit partial every 0.5s

# ── Groq Whisper Integration ─────────────────────────────────────────────────
# ── Shared Model (loaded once) ────────────────────────────────────────────────
_model: WhisperModel | None = None

def _get_model() -> WhisperModel | None:
    """Load the on-device STT model lazily so startup stays responsive."""
    global _model
    if _model is None:
        try:
            print(
                f"[STT] Loading local Faster-Whisper: {MODEL_SIZE} "
                f"({settings.STT_DEVICE}/{COMPUTE_TYPE})"
            )
            _model = WhisperModel(
                MODEL_SIZE,
                device=settings.STT_DEVICE,
                compute_type=COMPUTE_TYPE,
                cpu_threads=max(2, (os.cpu_count() or 4) - 1),
            )
        except Exception as exc:
            print(f"[STT] Failed to load local model: {exc}")
            return None
    return _model


def transcribe_local(pcm_bytes: bytes) -> str:
    """Transcribe VAD-gated PCM locally with an accuracy-oriented prompt."""
    if not pcm_bytes:
        return ""
    model = _get_model()
    if model is None:
        return ""
    audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        audio_array,
        beam_size=5,
        best_of=5,
        language="en",
        condition_on_previous_text=True,
        initial_prompt="FRIDAY, F.R.I.D.A.Y., WhatsApp, Chrome, Spotify, YouTube, note, message.",
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    return " ".join(segment.text.strip() for segment in segments).strip()

def listen_stream(partial_cb=None, stop_event=None) -> str:
    """
    Blocks while listening. Captures chunks, pushes them to a worker thread.
    Automatically returns the final string when SILENCE_LIMIT_FRAMES is crossed.
    """
    vad = webrtcvad.Vad(VAD_MODE)
    audio_queue = queue.Queue()
    
    done_event = threading.Event()
    final_result = [""]
    last_ui_text = [""]
    
    # ── STT Worker Thread ──────────────────────────────────────────────────
    def stt_worker():
        while True:
            item = audio_queue.get()
            if item["type"] == "quit":
                break
            
            # Optimize: Drain older partial items to prevent queue buildup and UI lag
            if item["type"] == "partial":
                try:
                    while True:
                        next_item = audio_queue.get_nowait()
                        if next_item["type"] == "final":
                            item = next_item
                            break
                        elif next_item["type"] == "partial":
                            item = next_item  # Keep only the latest partial
                        elif next_item["type"] == "quit":
                            item = next_item
                            break
                except queue.Empty:
                    pass
            
            if item["type"] == "quit":
                break
            
            audio_bytes = item["data"]
            
            try:
                # 1. Local transcription: unlimited use and microphone privacy.
                text = transcribe_local(audio_bytes)
                
                # 2. Retry only if an initial local pass returned no transcript.
                if not text:
                    model = _get_model()
                    if model:
                        print("[STT Engine] Groq returned empty — falling back to local Whisper...")
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                        segments, _ = model.transcribe(
                            audio_array,
                            beam_size=3,
                            language="en",
                            initial_prompt="F.R.I.D.A.Y., Friday, WhatsApp, Chrome, Laxman, Vaasavi, aka, message.",
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=400)
                        )
                        text = " ".join([seg.text for seg in segments]).strip()
                    else:
                        text = ""
                
                if item["type"] == "partial":
                    # Always call partial_cb if we have countdown or new text
                    current_countdown = item.get("countdown")
                    if partial_cb and (text != last_ui_text[0] or current_countdown is not None):
                        try:
                            partial_cb(text, countdown=current_countdown)
                            if text:
                                last_ui_text[0] = text
                        except:
                            pass
                elif item["type"] == "final":
                    # If the final transcribe (often padded with silence) returns empty, 
                    # fallback to the last valid partial text we generated.
                    final_result[0] = text if text.strip() else last_ui_text[0]
                    done_event.set()
                    break
                    
            except Exception as e:
                print(f"[STT Engine] Transcribe error: {e}")
                if item["type"] == "final":
                    done_event.set()
                    break

    worker_thread = threading.Thread(target=stt_worker, daemon=True)
    worker_thread.start()

    # ── sounddevice Mic Stream ────────────────────────────────────────────
    stream = None
    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SIZE // 2
        )
        with stream:
            buffer = []
            silence_counter = 0
            last_partial_time = time.time()
            has_spoken = False

            print("[STT Pipeline] Stream active. VAD gating is ON.")

            while not done_event.is_set():
                # Check external UI abort
                if stop_event and stop_event.is_set():
                    print("[STT Pipeline] External stop trigger intercepted.")
                    audio_queue.put({"type": "final", "data": b"".join(buffer)})
                    break

                try:
                    frame_array, overflowed = stream.read(FRAME_SIZE // 2)
                    frame = frame_array.tobytes()
                except Exception as e:
                    print(f"[STT Pipeline] Mic read error: {e}")
                    continue
                
                is_speech = vad.is_speech(frame, SAMPLE_RATE)

                if is_speech:
                    buffer.append(frame)
                    silence_counter = 0
                    has_spoken = True
                else:
                    silence_counter += 1
                    if has_spoken:
                        buffer.append(frame) # Keep silence internally so Whisper maintains flow context

                # State Logic
                if has_spoken:
                    now = time.time()
                    total_sil_limit = (SILENCE_LIMIT_FRAMES * FRAME_DURATION) / 1000.0
                    current_sil_s = (silence_counter * FRAME_DURATION) / 1000.0
                    countdown_val = int(math.ceil(total_sil_limit - current_sil_s)) if current_sil_s > 0.5 and current_sil_s <= total_sil_limit else None
                    
                    last_countdown = getattr(listen_stream, "_last_cd", None)
                    
                    # Emit partial periodically OR when the countdown ticks a full second
                    if (now - last_partial_time > PARTIAL_INTERVAL) or (countdown_val is not None and countdown_val != last_countdown):
                        audio_queue.put({"type": "partial", "data": b"".join(buffer), "countdown": countdown_val})
                        last_partial_time = now
                        listen_stream._last_cd = countdown_val

                    # 2. Silence Cutoff or Absolute duration limit
                    if silence_counter > SILENCE_LIMIT_FRAMES or len(buffer) >= 600:
                        print(f"[STT Pipeline] End of Speech detected ({silence_counter * FRAME_DURATION}ms). Buffer length: {len(buffer)}")
                        if partial_cb:
                            try:
                                partial_cb(last_ui_text[0], countdown=0)
                            except:
                                pass
                        audio_queue.put({"type": "final", "data": b"".join(buffer)})
                        break
                else:
                    # User never spoke -> Timeout
                    if silence_counter > INITIAL_TIMEOUT_FRAMES:
                        print(f"[STT Pipeline] Initial timeout. No speech detected.")
                        audio_queue.put({"type": "final", "data": b""})
                        break

    except Exception as e:
        print(f"[STT Pipeline] Mic Stream Crash: {e}")
        audio_queue.put({"type": "quit"})

    finally:
        pass

    # ── Cleanup ───────────────────────────────────────────────────────────
    # Wait for the worker to translate the final assembled chunk
    done_event.wait(timeout=5.0)
    audio_queue.put({"type": "quit"})
    worker_thread.join(timeout=1.0)

    final_text = final_result[0].strip()
    print(f"[STT Pipeline] Completed: {final_text!r}")
    return final_text

if __name__ == "__main__":
    def on_partial(text):
        print(f"\r[Live] {text}   ", end="", flush=True)

    try:
        print("[Demo] Listening...\n")
        res = listen_stream(partial_cb=on_partial)
        print(f"\n[Done] {res}")
    except KeyboardInterrupt:
        print("\nShutdown.")
