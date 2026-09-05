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

import collections
import io
import math
import os
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel

from config import settings
from stt.audio_prep import release_mic_blockers

# Phase 0 duplex controller (graceful fallback if import fails during tests)
try:
    from stt.duplex import duplex as _duplex  # type: ignore
except Exception:  # pragma: no cover
    _duplex = None  # type: ignore

# ── Configuration ────────────────────────────────────────────────────────────
MODEL_SIZE    = settings.STT_MODEL
SAMPLE_RATE   = 16000
FRAME_DURATION= 30       # ms 
FRAME_SIZE    = int(SAMPLE_RATE * FRAME_DURATION / 1000) * 2 # 960 bytes (16-bit PCM)
COMPUTE_TYPE  = settings.STT_COMPUTE_TYPE
VAD_MODE      = max(0, min(3, settings.STT_VAD_MODE))
SPEECH_RMS    = settings.STT_SPEECH_RMS
SPEECH_SNR_MULT = max(1.5, float(settings.STT_SPEECH_SNR_MULT))
NOISE_FLOOR_ALPHA = 0.05

# Timers (30ms frames)
SILENCE_LIMIT_FRAMES = max(
    10,
    int(round(settings.STT_SILENCE_TIMEOUT_S * 1000 / FRAME_DURATION)),
)
PRE_SPEECH_TIMEOUT_FRAMES = max(
    SILENCE_LIMIT_FRAMES * 2,
    int(round(settings.STT_PRE_SPEECH_TIMEOUT_S * 1000 / FRAME_DURATION)),
)
MIN_SPEECH_FRAMES = 4    # ~120ms sustained speech before arming capture
MIN_UTTERANCE_SPEECH_FRAMES = 8  # ~240ms of real speech before we transcribe
CALIBRATION_FRAMES = 10  # ~300ms ambient noise calibration at mic open
PARTIAL_INTERVAL  = 0.40  # Live companion preview
TRANSCRIBE_TIMEOUT_S    = 18.0

_groq_client = None
_model: WhisperModel | None = None
_partial_model: WhisperModel | None = None


def _groq_api_key() -> str:
    return (settings.GROQ_API_KEY or settings.STT_API_KEY or "").strip()


def _use_groq_stt() -> bool:
    provider = (settings.STT_PROVIDER or "auto").lower()
    if provider == "local":
        return False
    if provider == "groq":
        return bool(_groq_api_key())
    return bool(_groq_api_key())


def _get_groq_client():
    global _groq_client
    key = _groq_api_key()
    if not key:
        return None
    if _groq_client is None:
        from groq import Groq

        _groq_client = Groq(api_key=key)
    return _groq_client

def _device_supports_capture(device_id: int) -> bool:
    try:
        sd.check_input_settings(device=device_id, samplerate=SAMPLE_RATE, channels=1)
        return True
    except Exception:
        return False


def _hostapi_indices() -> dict[str, int]:
    """Map PortAudio host API names to indices (e.g. Windows WASAPI)."""
    out: dict[str, int] = {}
    try:
        for idx, api in enumerate(sd.query_hostapis()):
            out[(api.get("name") or "").lower()] = idx
    except Exception:
        pass
    return out


def _resolve_input_device() -> int | None:
    """Pick the microphone device for capture (-1 = OS default, with Realtek fallbacks)."""
    configured = settings.STT_INPUT_DEVICE
    if configured >= 0:
        return configured

    hostapis = _hostapi_indices()
    wasapi_idx = hostapis.get("windows wasapi")
    ds_idx = hostapis.get("windows directsound")
    wdm_idx = hostapis.get("windows wdm-ks")

    wasapi_arrays: list[int] = []
    wasapi_mics: list[int] = []
    ds_arrays: list[int] = []
    ds_mics: list[int] = []
    mme_mics: list[int] = []
    fallback: list[int] = []

    try:
        default = sd.default.device[0]
        if default is not None and int(default) >= 0:
            fallback.append(int(default))
    except Exception:
        pass

    try:
        for idx, dev in enumerate(sd.query_devices()):
            name = (dev.get("name") or "").lower()
            if dev.get("max_input_channels", 0) < 1:
                continue
            if any(skip in name for skip in ("stereo mix", "pc speaker", "output", "mapper")):
                continue
            hostapi = dev.get("hostapi")
            if wdm_idx is not None and hostapi == wdm_idx:
                continue

            is_array = "microphone array" in name
            is_mic = is_array or "microphone" in name or " mic" in name

            if wasapi_idx is not None and hostapi == wasapi_idx:
                if is_array:
                    wasapi_arrays.append(idx)
                elif is_mic:
                    wasapi_mics.append(idx)
            elif ds_idx is not None and hostapi == ds_idx:
                if is_array:
                    ds_arrays.append(idx)
                elif is_mic:
                    ds_mics.append(idx)
            elif is_mic:
                mme_mics.append(idx)
    except Exception:
        pass

    candidates = (
        wasapi_arrays
        + wasapi_mics
        + ds_arrays
        + ds_mics
        + mme_mics
        + fallback
    )
    seen: set[int] = set()
    for idx in candidates:
        if idx in seen:
            continue
        seen.add(idx)
        if _device_supports_capture(idx):
            try:
                dev = sd.query_devices(idx)
                api_name = sd.query_hostapis()[dev["hostapi"]]["name"]
                print(f"[STT] Selected input device {idx}: {dev['name']} ({api_name})")
            except Exception:
                pass
            return idx
    return None


def _frame_rms(frame_array: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame_array.astype(np.float32) ** 2)))


def _frame_is_speech(
    vad: webrtcvad.Vad,
    frame: bytes,
    frame_array: np.ndarray,
    noise_floor: list[float],
) -> bool:
    """WebRTC VAD plus adaptive RMS gate — rejects distant/background voices."""
    rms = _frame_rms(frame_array)
    vad_says = vad.is_speech(frame, SAMPLE_RATE)
    gate = max(18.0, noise_floor[0] * 1.15)
    is_speech = vad_says and (rms >= gate)
    if not is_speech:
        noise_floor[0] = noise_floor[0] * (1.0 - NOISE_FLOOR_ALPHA) + min(rms, 40.0) * NOISE_FLOOR_ALPHA
    return is_speech


def _get_partial_model() -> WhisperModel | None:
    """Fast on-device model for live partials — never hits Groq (avoids 429 + lag)."""
    global _partial_model
    if _partial_model is None:
        try:
            name = getattr(settings, "STT_PARTIAL_MODEL", "base.en") or "base.en"
            _partial_model = WhisperModel(
                name,
                device=settings.STT_DEVICE,
                compute_type=COMPUTE_TYPE,
                cpu_threads=max(2, (os.cpu_count() or 4) - 2),
            )
        except Exception as exc:
            print(f"[STT] Partial model load failed: {exc}")
            return None
    return _partial_model


def transcribe_partial_local(pcm_bytes: bytes) -> str:
    """Fast local preview transcript for companion UI only."""
    if len(pcm_bytes) < FRAME_SIZE * 5:
        return ""
    model = _get_partial_model()
    if model is None:
        return ""
    try:
        audio_array = (
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
        segments, _ = model.transcribe(
            audio_array,
            beam_size=1,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250),
            condition_on_previous_text=False,
            initial_prompt="F.R.I.D.A.Y., Friday",
        )
        parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
        return " ".join(parts).strip()
    except Exception:
        return ""


def warm_stt_models() -> None:
    """Pre-load partial + final models so the first companion utterance is not delayed."""
    _get_partial_model()
    _get_model()


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


def _is_meaningful_transcript(text: str) -> bool:
    cleaned = (text or "").strip().strip("\"'`")
    if len(cleaned) < 2:
        return False
    if cleaned.lower() in {".", ",", "you", "thank you.", "thanks for watching."}:
        return False
    return True


def transcribe_groq(pcm_bytes: bytes) -> str:
    """Transcribe via Groq Whisper (same API key as LLM). Retries on rate limits."""
    if not pcm_bytes or len(pcm_bytes) < FRAME_SIZE * 12:
        return ""
    client = _get_groq_client()
    if client is None:
        return ""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    audio_data = buf.getvalue()

    models = ("whisper-large-v3-turbo", "whisper-large-v3")
    last_exc: Exception | None = None
    for model_name in models:
        for attempt in range(2):
            try:
                response = client.audio.transcriptions.create(
                    file=("speech.wav", audio_data),
                    model=model_name,
                    language="en",
                    prompt="FRIDAY, Friday, WhatsApp, Chrome, Spotify, YouTube, play music, headlines.",
                    temperature=0.0,
                )
                text = (response.text or "").strip()
                if _is_meaningful_transcript(text):
                    return text
                break
            except Exception as exc:
                last_exc = exc
                err = str(exc).lower()
                if "429" in err or "rate" in err:
                    print("[STT] Groq rate limited — falling back to local STT")
                    return ""
                if attempt < 1:
                    time.sleep(0.6)
                    continue
                print(f"[STT] Groq {model_name} failed: {exc}")
                break
    if last_exc:
        print(f"[STT] Groq transcription failed after retries: {last_exc}")
    return ""


def transcribe_audio(pcm_bytes: bytes, *, prefer_groq: bool = False) -> str:
    """Groq-first when configured, with local Faster-Whisper fallback."""
    if not pcm_bytes:
        return ""
    if prefer_groq or _use_groq_stt():
        text = transcribe_groq(pcm_bytes)
        if _is_meaningful_transcript(text):
            return text
    local = transcribe_local(pcm_bytes)
    return local if _is_meaningful_transcript(local) else ""


def transcribe_local(pcm_bytes: bytes) -> str:
    """Transcribe VAD-gated PCM locally with an accuracy-oriented prompt."""
    if not pcm_bytes:
        return ""
    model = _get_model()
    if model is None:
        return ""
    audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    # Audio buffer is already WebRTC VAD gated; disabling internal vad_filter prevents clipping
    kwargs = dict(
        beam_size=3,
        language="en",
        condition_on_previous_text=False,
        initial_prompt="Hey Friday, compare Asus and MacBook, what's on my screen, open Chrome, Spotify, code, terminal, weather, time.",
        vad_filter=False,
        temperature=0.0,
    )
    segments, _ = model.transcribe(audio_array, **kwargs)
    return " ".join(segment.text.strip() for segment in segments).strip()

_last_mic_ok = True
_last_had_speech = False


def last_listen_mic_ok() -> bool:
    """Whether the most recent listen_stream opened the mic successfully."""
    return _last_mic_ok


def last_listen_had_speech() -> bool:
    """Whether near-field speech was detected during the most recent listen."""
    return _last_had_speech


def listen_stream(partial_cb=None, stop_event=None) -> str:
    """
    Blocks while listening. Captures chunks, pushes them to a worker thread.
    Automatically returns the final string when SILENCE_LIMIT_FRAMES is crossed.
    """
    global _last_mic_ok, _last_had_speech
    _last_mic_ok = True
    _last_had_speech = False

    def _emit_partial(text: str, countdown: int | None = None, phase: str | None = None) -> None:
        if not partial_cb:
            return
        out_text = text if (text and text.strip()) else (last_ui_text[0] if last_ui_text[0].strip() else "")
        try:
            if phase is not None:
                partial_cb(out_text, countdown=countdown, phase=phase)
            else:
                partial_cb(out_text, countdown=countdown)
        except TypeError:
            partial_cb(out_text, countdown=countdown)
        except Exception:
            pass

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
            # Duplex hard gate: drop any queued audio if we are in TTS/tail
            # (prevents stale buffered speaker audio from being transcribed after mute)
            if _duplex is not None and item["type"] in ("partial", "final"):
                try:
                    if _duplex.is_tts_active() or _duplex.is_in_tail():
                        if item["type"] == "final":
                            # treat as phantom — no command
                            final_result[0] = ""
                            done_event.set()
                            break
                        continue
                except Exception:
                    pass
            
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
                if item["type"] == "partial":
                    if done_event.is_set():
                        continue
                    max_partial_bytes = int(10.0 * SAMPLE_RATE * 2)
                    partial_audio = audio_bytes if len(audio_bytes) <= max_partial_bytes else audio_bytes[-max_partial_bytes:]
                    if len(partial_audio) >= FRAME_SIZE * 5:
                        text = transcribe_partial_local(partial_audio)
                        if text:
                            last_ui_text[0] = text
                    current_countdown = item.get("countdown")
                    _emit_partial(last_ui_text[0], countdown=current_countdown)
                    continue

                text = transcribe_audio(audio_bytes, prefer_groq=_use_groq_stt())
                if not text and not _use_groq_stt():
                    model = _get_model()
                    if model:
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                        segments, _ = model.transcribe(
                            audio_array,
                            beam_size=3,
                            language="en",
                            initial_prompt="F.R.I.D.A.Y., Friday, WhatsApp, Chrome, Laxman, Vaasavi, aka, message.",
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=400),
                        )
                        text = " ".join(seg.text for seg in segments).strip()

                # ── Duplex echo / tail filter (Phase 0) ─────────────────
                if text and _duplex is not None:
                    try:
                        drop, reason = _duplex.should_drop_transcript(text)
                        if drop:
                            print(f"[STT Duplex] Dropped final transcript ({reason}): {text!r}")
                            text = ""
                            # also clear partial fallback if it was echo
                            if last_ui_text[0]:
                                d2, _ = _duplex.should_drop_transcript(last_ui_text[0])
                                if d2:
                                    last_ui_text[0] = ""
                    except Exception as exc:
                        print(f"[STT Duplex] filter error: {exc}")

                if item["type"] == "final":
                    # If the final transcribe (often padded with silence) returns empty,
                    # fallback to the last valid partial text we generated.
                    chosen = text if text.strip() else last_ui_text[0]
                    # final fallback must also pass duplex
                    if chosen and _duplex is not None:
                        try:
                            d3, r3 = _duplex.should_drop_transcript(chosen)
                            if d3:
                                print(f"[STT Duplex] Dropped fallback ({r3}): {chosen!r}")
                                chosen = ""
                        except Exception:
                            pass
                    final_result[0] = chosen
                    done_event.set()
                    break
                    
            except Exception as e:
                print(f"[STT Engine] Transcribe error: {e}")
                if item["type"] == "final":
                    done_event.set()
                    break

    worker_thread = threading.Thread(target=stt_worker, daemon=True)
    worker_thread.start()

    release_mic_blockers()
    # ── Duplex hard-mute wait (Phase 0) ────────────────────────────────
    # Wait for TTS + acoustic tail before arming the mic.
    # Previously waited max 8s on is_tts_active only; now respects tail
    # and allows early exit on stop_event (barge-in via hotkey).
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            if _duplex is not None:
                if _duplex.can_listen():
                    break
            else:
                from tts.pocket_tts import is_tts_active

                if not is_tts_active():
                    break
        except Exception:
            break
        time.sleep(0.12)
    # Acoustic tail extra guard — ensure we are fully clear
    if _duplex is not None:
        remain = _duplex.tail_remaining_ms()
        if remain > 0:
            time.sleep(min(remain / 1000.0, 0.6))
    else:
        time.sleep(0.2)

    # ── sounddevice Mic Stream (callback — blocking read fails on Windows WDM-KS) ─
    frame_queue: queue.Queue = queue.Queue(maxsize=60)
    input_device = _resolve_input_device()

    def audio_callback(indata: np.ndarray, _frames: int, _cb_time, _status) -> None:
        if done_event.is_set():
            return
        if frame_queue.full():
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
        frame_queue.put_nowait(indata.copy())

    try:
        stream_kwargs = dict(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SIZE // 2,
            latency="high",
            callback=audio_callback,
        )
        if input_device is not None:
            stream_kwargs["device"] = input_device
        device_label = (
            sd.query_devices(input_device)["name"]
            if input_device is not None
            else "default"
        )
        stt_backend = "groq" if _use_groq_stt() else "local"
        print(
            f"[STT Pipeline] Stream active on '{device_label}'. "
            f"VAD mode={VAD_MODE}, RMS gate={SPEECH_RMS}, "
            f"silence={SILENCE_LIMIT_FRAMES * FRAME_DURATION}ms, STT={stt_backend}."
        )
        with sd.InputStream(**stream_kwargs):
            preroll_buffer = collections.deque(maxlen=16)  # 16 frames * 30ms = 480ms pre-speech audio
            buffer = []
            silence_counter = 0
            speech_streak = 0
            speech_frame_count = 0
            elapsed_frames = 0
            calibration_frames = 0
            last_partial_time = time.time()
            has_spoken = False
            speech_notified = False
            noise_floor = [max(25.0, SPEECH_RMS * 0.25)]
            calibration_rms: list[float] = []
            last_countdown: int | None = None

            while not done_event.is_set():
                elapsed_frames += 1
                # ── Duplex hard mute (Phase 0) ──────────────────────────
                # While TTS is speaking or tail is active, ignore all mic frames.
                # This prevents speaker echo from arming VAD / filling buffer.
                if _duplex is not None:
                    try:
                        if _duplex.is_tts_active() or _duplex.is_in_tail():
                            # drop any partially buffered utterance
                            if has_spoken or buffer:
                                buffer.clear()
                                preroll_buffer.clear()
                                has_spoken = False
                                speech_notified = False
                                speech_frame_count = 0
                                silence_counter = 0
                                speech_streak = 0
                                last_countdown = None
                            # drain queue to keep latency low
                            try:
                                while not frame_queue.empty():
                                    frame_queue.get_nowait()
                            except Exception:
                                pass
                            time.sleep(0.05)
                            continue
                    except Exception:
                        pass
                # Check external UI abort
                if stop_event and stop_event.is_set():
                    print("[STT Pipeline] External stop trigger intercepted.")
                    audio_queue.put({"type": "final", "data": b"".join(buffer)})
                    break

                try:
                    frame_array = frame_queue.get(timeout=0.15)
                except queue.Empty:
                    continue

                frame_array = frame_array.flatten()
                frame = frame_array.tobytes()

                frame_rms = _frame_rms(frame_array)
                if calibration_frames < CALIBRATION_FRAMES:
                    calibration_frames += 1
                    calibration_rms.append(frame_rms)
                    preroll_buffer.append(frame)
                    if calibration_frames == CALIBRATION_FRAMES and calibration_rms:
                        ambient = float(np.median(calibration_rms))
                        noise_floor[0] = max(18.0, min(ambient, SPEECH_RMS * 0.70))
                        print(
                            f"[STT Pipeline] Mic calibrated — noise floor={noise_floor[0]:.1f}, "
                            f"gate={max(SPEECH_RMS, noise_floor[0] * SPEECH_SNR_MULT):.1f}"
                        )
                        _emit_partial("", phase="open")
                    continue

                preroll_buffer.append(frame)
                is_speech = _frame_is_speech(vad, frame, frame_array, noise_floor)

                if is_speech:
                    speech_streak += 1
                    silence_counter = 0
                    if not has_spoken and speech_streak >= MIN_SPEECH_FRAMES:
                        has_spoken = True
                        # Prepend the pre-roll buffer so the start of the sentence is preserved
                        buffer.extend(preroll_buffer)
                        speech_frame_count += len(preroll_buffer)
                        preroll_buffer.clear()
                        if not speech_notified:
                            speech_notified = True
                            _last_had_speech = True
                            _emit_partial("", phase="hearing")
                    if has_spoken:
                        buffer.append(frame)
                        speech_frame_count += 1
                else:
                    speech_streak = 0
                    silence_counter += 1
                    if has_spoken:
                        buffer.append(frame) # Keep silence internally so Whisper maintains flow context

                # Early preview while waiting for the user to speak.
                if not has_spoken and is_speech and speech_streak >= 1:
                    now = time.time()
                    if now - last_partial_time > PARTIAL_INTERVAL * 1.5:
                        _emit_partial("", phase="hearing")
                        last_partial_time = now

                # State Logic
                if has_spoken:
                    now = time.time()
                    total_sil_limit = (SILENCE_LIMIT_FRAMES * FRAME_DURATION) / 1000.0
                    current_sil_s = (silence_counter * FRAME_DURATION) / 1000.0
                    countdown_val = int(math.ceil(total_sil_limit - current_sil_s)) if current_sil_s > 0.2 and current_sil_s <= total_sil_limit else None
                    
                    countdown_changed = (countdown_val is not None and countdown_val != last_countdown)
                    
                    # Emit partial periodically OR when the countdown ticks a full second
                    if (now - last_partial_time > PARTIAL_INTERVAL) or countdown_changed:
                        audio_queue.put({"type": "partial", "data": b"".join(buffer), "countdown": countdown_val})
                        last_partial_time = now
                        last_countdown = countdown_val

                    # End utterance after ~1s silence once the user has spoken.
                    if silence_counter > SILENCE_LIMIT_FRAMES or len(buffer) >= 600:
                        if speech_frame_count < MIN_UTTERANCE_SPEECH_FRAMES and len(buffer) < 600:
                            print(
                                f"[STT Pipeline] False start ignored "
                                f"(speech_frames={speech_frame_count}, buffer={len(buffer)})"
                            )
                            has_spoken = False
                            speech_notified = False
                            speech_frame_count = 0
                            silence_counter = 0
                            last_countdown = None
                            buffer.clear()
                            last_ui_text[0] = ""
                            _emit_partial("", countdown=0, phase="open")
                            continue
                        print(f"[STT Pipeline] End of Speech detected ({silence_counter * FRAME_DURATION}ms). Buffer length: {len(buffer)}")
                        _emit_partial(last_ui_text[0], countdown=0, phase="thinking")
                        audio_queue.put({"type": "final", "data": b"".join(buffer)})
                        break
                else:
                    # Keep mic open until the user speaks or the pre-speech window expires.
                    active_frames = elapsed_frames - CALIBRATION_FRAMES
                    if active_frames > PRE_SPEECH_TIMEOUT_FRAMES:
                        print("[STT Pipeline] Pre-speech timeout — no near-field speech detected.")
                        _emit_partial("", countdown=0, phase="open")
                        audio_queue.put({"type": "final", "data": b""})
                        break

    except Exception as e:
        _last_mic_ok = False
        print(f"[STT Pipeline] Mic Stream Crash: {e}")
        audio_queue.put({"type": "quit"})

    finally:
        pass

    # ── Cleanup ───────────────────────────────────────────────────────────
    # Wait for the worker to translate the final assembled chunk
    done_event.wait(timeout=TRANSCRIBE_TIMEOUT_S)
    audio_queue.put({"type": "quit"})
    worker_thread.join(timeout=1.0)

    final_text = final_result[0].strip()
    # Final duplex guard — if this transcript slipped through, drop it here
    if final_text and _duplex is not None:
        try:
            drop, reason = _duplex.should_drop_transcript(final_text)
            if drop:
                print(f"[STT Duplex] Dropped returned transcript ({reason}): {final_text!r}")
                final_text = ""
        except Exception:
            pass
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
