"""
clone_tts.py — FRIDAY from friday-voice.wav
================================================
XTTS-v2 speaker clone. Chunk-streams so the first audio starts before
the sentence finishes generating. Next sentence generates while this one plays.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

os.environ.setdefault("COQUI_TOS_AGREED", "1")

VOICES_DIR = Path(__file__).with_name("voices")
VOICE_CLEAN = VOICES_DIR / "friday-voice.wav"
COND_CACHE = VOICES_DIR / ".friday_cond6.wav"

_lock = threading.Lock()
_xtts = None
_gpt_cond_latent = None
_speaker_embedding = None
_sample_rate = 24000
_out_rate: int | None = None

_SPEED = 1.06
_TEMPERATURE = 0.72
_GAIN = 0.72
_WRITE_FRAMES = 2048
_STREAM_CHUNK = 16
_FIRST_AUDIO_SEC = 0.12


def reference_wav() -> str:
    if not VOICE_CLEAN.exists() or VOICE_CLEAN.stat().st_size <= 1000:
        raise FileNotFoundError(f"Missing FRIDAY clone source: {VOICE_CLEAN}")
    return str(VOICE_CLEAN.resolve())


def _best_window_wav(src: str, seconds: float = 6.0) -> str:
    """Cut the loudest `seconds` of the clean clip so style isn't averaged away."""
    src_path = Path(src)
    if (
        COND_CACHE.exists()
        and COND_CACHE.stat().st_size > 1000
        and COND_CACHE.stat().st_mtime >= src_path.stat().st_mtime
    ):
        return str(COND_CACHE)
    with wave.open(src, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
        width = w.getsampwidth()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    win = int(sr * seconds)
    if audio.size > win:
        hop = max(1, sr // 10)
        best_i, best = 0, -1.0
        for i in range(0, audio.size - win + 1, hop):
            seg = audio[i : i + win]
            rms = float(np.sqrt(np.mean(seg * seg)))
            if rms > best:
                best, best_i = rms, i
        audio = audio[best_i : best_i + win]
    peak = float(np.max(np.abs(audio))) + 1e-6
    pcm = np.clip(audio / peak * 28000.0, -32768, 32767).astype(np.int16)
    with wave.open(str(COND_CACHE), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    print(f"[CloneTTS] Condition window {seconds:.0f}s from {src_path.name} -> {COND_CACHE.name}")
    return str(COND_CACHE)


def _ensure_xtts():
    global _xtts, _gpt_cond_latent, _speaker_embedding, _sample_rate
    if _xtts is not None and _gpt_cond_latent is not None and _speaker_embedding is not None:
        return
    with _lock:
        if _xtts is not None and _gpt_cond_latent is not None and _speaker_embedding is not None:
            return
        from TTS.api import TTS

        ref = _best_window_wav(reference_wav(), 6.0)
        print(f"[CloneTTS] Cloning from {ref}")
        model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
        try:
            model = model.to("cpu")
        except Exception:
            pass
        xtts = model.synthesizer.tts_model
        # Single 6s chunk (len == chunk) — no averaging that washes out timbre.
        gpt_cond_latent, speaker_embedding = xtts.get_conditioning_latents(
            audio_path=ref,
            max_ref_length=6,
            gpt_cond_len=6,
            gpt_cond_chunk_len=6,
            sound_norm_refs=False,
        )
        _xtts = xtts
        _gpt_cond_latent = gpt_cond_latent
        _speaker_embedding = speaker_embedding
        _sample_rate = int(getattr(xtts, "output_sample_rate", None) or 24000)
        print("[CloneTTS] FRIDAY clone ready (friday-voice.wav, 6s peak window).")


def _groups(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    return parts or [text]


def _to_pcm(chunk) -> np.ndarray:
    if chunk is None:
        return np.zeros(0, dtype=np.float32)
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    x = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x
    x -= float(np.mean(x))
    return np.clip(x * _GAIN, -1.0, 1.0).astype(np.float32)


def _output_rate(model_rate: int) -> int:
    global _out_rate
    if _out_rate is not None:
        return _out_rate
    try:
        sd.check_output_settings(samplerate=model_rate, channels=1, dtype="float32")
        _out_rate = model_rate
    except Exception:
        try:
            _out_rate = int(sd.query_devices(kind="output")["default_samplerate"])
        except Exception:
            _out_rate = model_rate
    return _out_rate


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    try:
        from math import gcd
        from scipy.signal import resample_poly

        g = gcd(src, dst)
        return resample_poly(audio.astype(np.float64), dst // g, src // g).astype(np.float32)
    except Exception:
        n = max(1, int(round(len(audio) * dst / src)))
        return np.interp(
            np.linspace(0, len(audio) - 1, n, dtype=np.float64),
            np.arange(len(audio), dtype=np.float64),
            audio.astype(np.float64),
        ).astype(np.float32)


def _iter_sentence_chunks(text: str):
    for chunk in _xtts.inference_stream(
        text,
        "en",
        _gpt_cond_latent,
        _speaker_embedding,
        stream_chunk_size=_STREAM_CHUNK,
        overlap_wav_len=1024,
        temperature=_TEMPERATURE,
        speed=_SPEED,
        length_penalty=1.0,
        repetition_penalty=6.0,
        top_p=0.85,
        top_k=50,
        enable_text_splitting=False,
    ):
        pcm = _to_pcm(chunk)
        if pcm.size:
            yield pcm


def synthesize(text: str) -> tuple[np.ndarray, int]:
    _ensure_xtts()
    from tts.pocket_tts import clean_text_for_speech

    clean = clean_text_for_speech(text)
    pieces: list[np.ndarray] = []
    with _lock:
        for part in _groups(clean):
            pieces.extend(_iter_sentence_chunks(part))
    if not pieces:
        return np.zeros(0, dtype=np.float32), _sample_rate
    return np.concatenate(pieces), _sample_rate


def _write_pcm(stream, samples: np.ndarray, stop_event) -> None:
    x = np.ascontiguousarray(samples.reshape(-1, 1), dtype=np.float32)
    i = 0
    n = len(x)
    while i < n:
        if stop_event is not None and stop_event.is_set():
            return
        end = min(n, i + _WRITE_FRAMES)
        stream.write(x[i:end])
        i = end


def speak_cloned(text: str, stop_event=None) -> bool:
    if not text or not text.strip():
        return False
    _ensure_xtts()
    from tts.pocket_tts import clean_text_for_speech

    clean = clean_text_for_speech(text.strip())
    groups = _groups(clean)
    if not groups:
        return False

    ready: queue.Queue = queue.Queue(maxsize=8)
    _END = object()

    def produce():
        try:
            for idx, part in enumerate(groups):
                if stop_event is not None and stop_event.is_set():
                    break
                t0 = time.perf_counter()
                n = 0
                with _lock:
                    for pcm in _iter_sentence_chunks(part):
                        n += pcm.size
                        ready.put(pcm)
                print(
                    f"[CloneTTS] streamed line {idx + 1}/{len(groups)} "
                    f"{(time.perf_counter() - t0) * 1000:.0f}ms dur={n / _sample_rate:.2f}s",
                    flush=True,
                )
        finally:
            ready.put(_END)

    worker = threading.Thread(target=produce, daemon=True, name="friday-tts-gen")
    worker.start()

    out_rate = _output_rate(_sample_rate)
    pre: list[np.ndarray] = []
    pre_n = 0
    first_needed = int(_sample_rate * _FIRST_AUDIO_SEC)
    t_wait = time.perf_counter()
    while pre_n < first_needed:
        item = ready.get()
        if item is _END:
            break
        pre.append(item)
        pre_n += item.size
    if pre_n == 0:
        return False

    first = np.concatenate(pre)
    if out_rate != _sample_rate:
        first = _resample(first, _sample_rate, out_rate)
    ttfa = (time.perf_counter() - t_wait) * 1000
    print(
        f"[CloneTTS] stream start ttfa={ttfa:.0f}ms @ {out_rate}Hz "
        f"speed={_SPEED} temp={_TEMPERATURE} src=friday-voice.wav",
        flush=True,
    )

    stream_kwargs = dict(
        samplerate=out_rate,
        channels=1,
        dtype="float32",
        blocksize=_WRITE_FRAMES,
        latency="high",
    )
    try:
        stream = sd.OutputStream(**stream_kwargs)
    except Exception:
        stream_kwargs.pop("latency", None)
        stream = sd.OutputStream(**stream_kwargs)

    silence = np.zeros((_WRITE_FRAMES, 1), dtype=np.float32)
    played = False
    try:
        with stream:
            _write_pcm(stream, first, stop_event)
            played = True
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    item = ready.get(timeout=_WRITE_FRAMES / float(out_rate))
                except queue.Empty:
                    stream.write(silence)
                    continue
                if item is _END:
                    break
                if item is None or getattr(item, "size", 0) == 0:
                    continue
                if out_rate != _sample_rate:
                    item = _resample(item, _sample_rate, out_rate)
                _write_pcm(stream, item, stop_event)
    except Exception as exc:
        print(f"[CloneTTS] stream error: {exc}")
    finally:
        worker.join(timeout=0.2)
    return played


def warm_up() -> None:
    _ensure_xtts()
    print("[CloneTTS] FRIDAY clone warmed.")
