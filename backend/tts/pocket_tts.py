import os
import re
import queue
import threading
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
from pocket_tts import TTSModel

_DEFAULT_VOICE_WAV = Path(__file__).with_name("voices") / "friday-voice.wav"
# Official gated clone checkpoint from pocket-tts english.yaml.
CLONE_WEIGHTS = (
    "hf://kyutai/pocket-tts/languages/english/model.safetensors"
    "@39592ff23c9ef80098bb74895d104c26275fe2c9"
)
# Optimal pocket-tts parameters for clear, natural, studio-quality speech
_LOAD_TEMP = 0.30
_LOAD_LSD_STEPS = 1
_LOAD_NOISE_CLAMP = None
_LOAD_EOS_THRESHOLD = -4.0
_FRAMES_AFTER_EOS = None
_PEAK_TARGET = 0.85
_MAX_BOOST = 3.5

_model = None
_voice_state = None
_hardware_sample_rate = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False
_active_stream = None
_playback_stream = None
_afplay_proc = None


# ── Duplex / echo helpers (Phase 0) ──────────────────────────────────────────

def _mic_is_open() -> bool:
    """True when STT mic InputStream is active — sd.stop() would kill it on macOS."""
    try:
        from services.runtime_state import flags

        if bool(getattr(flags, "is_listening", False)):
            return True
    except Exception:
        pass
    try:
        from stt.duplex import duplex as _dup

        # If duplex says TTS is active, mic should not be open — but check anyway
        # via voice_loop's is_listening flag above is authoritative.
        _ = _dup
    except Exception:
        pass
    return False


def _safe_sd_stop() -> None:
    """Call sd.stop() only when mic is not open — prevents PortAudio abort on macOS."""
    if _mic_is_open():
        print("[TTS] sd.stop() skipped — mic is open (duplex half-duplex guard)")
        return
    try:
        sd.stop()
    except Exception:
        pass


def _duplex_notify_start(text: str) -> None:
    try:
        from stt.duplex import duplex as _dup

        _dup.notify_tts_start(text)
    except Exception:
        pass
    # also mirror to runtime flags for filter.py fallback
    try:
        from services.runtime_state import flags

        if text:
            flags.last_assistant_response = text.strip()
    except Exception:
        pass


def _duplex_notify_end() -> None:
    try:
        from stt.duplex import duplex as _dup

        _dup.notify_tts_end()
    except Exception:
        pass


def _resolve_voice_wav_path() -> Path:
    """Always resolve to the exact local WAV used for FRIDAY voice cloning."""
    try:
        from config import settings

        custom = (getattr(settings, "FRIDAY_VOICE_PATH", "") or "").strip()
        if custom:
            path = Path(custom).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"FRIDAY_VOICE_PATH not found: {path}")
            if path.suffix.lower() != ".wav":
                raise ValueError(f"FRIDAY_VOICE_PATH must be a .wav file: {path}")
            return path
    except ImportError:
        pass

    voices_dir = Path(__file__).with_name("voices")
    for name in ("FridayVoice2.wav", "friday-voice.wav"):
        candidate = voices_dir / name
        if candidate.is_file():
            return candidate.resolve()

    path = _DEFAULT_VOICE_WAV.resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"FRIDAY voice file missing: {path}. "
            "Place FridayVoice2.wav or friday-voice.wav in backend/tts/voices/ or set FRIDAY_VOICE_PATH."
        )
    return path


def _voice_state_cache_path(wav_path: Path) -> Path:
    return wav_path.with_suffix(".safetensors")


def _load_friday_voice_state(model: TTSModel, wav_path: Path) -> dict:
    """
    Build model state from friday-voice.wav.

    The safetensors cache is skipped — exported state lacks ``current_end`` and
    breaks ``generate_audio`` (pocket-tts issue). Always encode from the WAV.
    """
    print(
        f"[TTS] Encoding FRIDAY voice from WAV: {wav_path} "
        f"({wav_path.stat().st_size} bytes, voice cloning)"
    )
    return model.get_state_for_audio_prompt(wav_path, truncate=True)


def _hf_token() -> str:
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    if token:
        return token
    try:
        from config import settings

        token = (getattr(settings, "HF_TOKEN", "") or "").strip()
    except Exception:
        token = ""
    return token


def _require_cloning_weights() -> None:
    from pocket_tts.utils.utils import download_if_necessary

    token = _hf_token()
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set. Put a Hugging Face token with access to "
            "kyutai/pocket-tts in backend/.env"
        )
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    try:
        from huggingface_hub import login

        login(token=token, add_to_git_credential=False)
    except Exception as exc:
        print(f"[TTS] Hugging Face login warning: {type(exc).__name__}")
    try:
        download_if_necessary(CLONE_WEIGHTS)
    except Exception as exc:
        raise RuntimeError(
            "Could not download Pocket TTS cloning weights from kyutai/pocket-tts. "
            f"Accept the model terms on Hugging Face, then retry. ({type(exc).__name__})"
        ) from exc


def _get_hardware_sample_rate() -> int:
    global _hardware_sample_rate
    if _hardware_sample_rate is not None:
        return _hardware_sample_rate
    model_rate = 24000
    if _model is not None and hasattr(_model, "sample_rate"):
        model_rate = int(_model.sample_rate)
    try:
        sd.check_output_settings(samplerate=model_rate)
        _hardware_sample_rate = model_rate
    except Exception:
        try:
            device_info = sd.query_devices(kind="output")
            _hardware_sample_rate = int(device_info["default_samplerate"])
        except Exception:
            _hardware_sample_rate = model_rate
    return _hardware_sample_rate


def _resample_audio(audio, original_rate: int, target_rate: int) -> np.ndarray:
    """Resample using bandlimited polyphase filtering if rates differ; never naive linear interpolation."""
    if original_rate == target_rate:
        return np.asarray(audio, dtype=np.float32).reshape(-1, 1)
    src = np.asarray(audio, dtype=np.float32).flatten()
    if src.size == 0:
        return np.zeros((0, 1), dtype=np.float32)
    try:
        import scipy.signal as sps
        import math
        gcd = math.gcd(int(original_rate), int(target_rate))
        up = int(target_rate // gcd)
        down = int(original_rate // gcd)
        resampled = sps.resample_poly(src, up, down)
        return resampled.astype(np.float32).reshape(-1, 1)
    except Exception:
        return src.reshape(-1, 1)


def _postprocess(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Clean DC offset and calibrate peak gain without frequency/phase distortion."""
    if samples.size == 0:
        return samples.astype(np.float32)
    x = samples.astype(np.float32).reshape(-1).copy()
    x -= np.mean(x)
    peak = float(np.max(np.abs(x)))
    if peak > 1e-5:
        target_peak = _PEAK_TARGET
        gain = min(target_peak / peak, _MAX_BOOST)
        x *= gain
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _audio_is_safe(samples: np.ndarray, sample_rate: int) -> tuple[bool, str]:
    if samples is None or samples.size == 0:
        return False, "empty"
    if samples.size < max(16, sample_rate // 100):
        return False, "too-short"
    if not np.isfinite(samples).all():
        return False, "nan/inf"
    x = samples.astype(np.float64).reshape(-1)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    if peak > 1.05:
        return False, f"overrange peak={peak:.3f}"
    return True, f"ok peak={peak:.3f} rms={rms:.3f} dur={len(x) / sample_rate:.2f}s"


def num_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    
    if n < 20:
        return ones[n]
    elif n < 100:
        suffix = "-" + ones[n % 10] if n % 10 != 0 else ""
        return tens[n // 10] + suffix
    elif n < 1000:
        suffix = " " + num_to_words(n % 100) if n % 100 != 0 else ""
        return ones[n // 100] + " hundred" + suffix
    else:
        if n % 1000 == 0:
            return ones[n // 1000] + " thousand"
        high = n // 100
        low = n % 100
        if 10 <= high <= 99:
            high_word = num_to_words(high)
            if low == 0:
                return high_word + " hundred"
            elif low < 10:
                return high_word + " o-" + num_to_words(low)
            else:
                return high_word + " " + num_to_words(low)
        return str(n)


def ordinal_to_words(n: int) -> str:
    if n == 0:
        return "zeroth"
    ord_tens = ["", "", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth", "eightieth", "ninetieth"]
    ord_ones = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth"]
    
    if n < 20:
        return ord_ones[n]
    elif n < 100:
        if n % 10 == 0:
            return ord_tens[n // 10]
        else:
            tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
            return tens[n // 10] + "-" + ord_ones[n % 10]
    return str(n)


def date_to_words(day_str: str, month_str: str, year_str: str) -> str:
    try:
        day = int(day_str)
        month = int(month_str)
        year = int(year_str)
        
        months = ["", "January", "February", "March", "April", "May", "June", 
                  "July", "August", "September", "October", "November", "December"]
        
        m_word = months[month] if 1 <= month <= 12 else str(month)
        day_ord = ordinal_to_words(day)
        year_word = num_to_words(year)
        
        return f"the {day_ord} of {m_word}, {year_word}"
    except Exception:
        return f"{day_str}-{month_str}-{year_str}"


def time_to_words(hour_str: str, minute_str: str, period_str: str = None) -> str:
    try:
        h = int(hour_str)
        m = int(minute_str)
        
        if not period_str:
            if h >= 12:
                period_str = "P M"
                if h > 12:
                    h -= 12
            else:
                period_str = "A M"
                if h == 0:
                    h = 12
        else:
            period_str = " ".join(list(period_str.upper().replace(".", "").strip()))
            
        h_word = num_to_words(h)
        
        if m == 0:
            m_word = "o'clock"
        elif m < 10:
            m_word = f"o-{num_to_words(m)}"
        else:
            m_word = num_to_words(m)
            
        return f"{h_word} {m_word} {period_str}".strip()
    except Exception:
        return f"{hour_str}:{minute_str} {period_str or ''}".strip()


def normalize_numbers_for_speech(text: str) -> str:
    # 1. Dates (DD-MM-YYYY or DD/MM/YYYY)
    def date_repl(match):
        day, month, year = match.groups()
        return date_to_words(day, month, year)
    text = re.sub(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", date_repl, text)

    # 2. Times (HH:MM AM/PM or HH:MM:SS or HH:MM)
    def time_repl(match):
        hour, minute, sec, period = match.groups()
        return time_to_words(hour, minute, period)
    text = re.sub(
        r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([aApP]\.?[mM]\.?)?\b",
        time_repl,
        text
    )

    # 3. Ordinals (31st, 2nd, 5th, etc.)
    def ord_repl(match):
        return ordinal_to_words(int(match.group(1)))
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", ord_repl, text, flags=re.IGNORECASE)

    # 4. Standalone numbers
    def num_repl(match):
        num = match.group(0)
        val = int(num)
        if val <= 9999:
            return num_to_words(val)
        return num
    text = re.sub(r"\b\d{1,4}\b", num_repl, text)

    return text


def _normalize_friday_pronunciation(text: str) -> str:
    """Map FRIDAY acronym variants to the natural word 'Friday' for TTS."""
    text = re.sub(
        r"\bF\.?\s*R\.?\s*I\.?\s*D\.?\s*A\.?\s*Y\.?\b",
        "Friday",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bFRIDAY\b", "Friday", text)
    text = re.sub(r"\bF\s+R\s+I\s+D\s+A\s+Y\b", "Friday", text, flags=re.IGNORECASE)
    return text


_SPEECH_SECTION_LABEL = re.compile(
    r"\b(?:OPENING|PROCESSING|CORE\s+DELIVERY|ACTION|CLOSE|DELIVERY|BRIEFING|STATUS)\s*:?\s*",
    re.IGNORECASE,
)
_PROCESSING_FILLER = re.compile(
    r"\b(?:PROCESSING|RUNNING\s+SYSTEM\s+CHECKS?)\b[\s.…]*",
    re.IGNORECASE,
)


def _strip_speech_template_markers(text: str) -> str:
    """Remove LLM stage labels and robotic filler before TTS."""
    text = _SPEECH_SECTION_LABEL.sub("", text)
    text = _PROCESSING_FILLER.sub("", text)
    # Collapse ellipsis runs — they cause choppy per-chunk synthesis
    text = re.sub(r"(?:\.{2,}|…)+", ", ", text)
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    return text


def clean_text_for_speech(text: str) -> str:
    """Trim formatting noise so the cloned voice stays natural."""
    try:
        from tts.pronunciation import apply_pronunciation_fixes

        text = apply_pronunciation_fixes(text)
    except Exception:
        pass
    text = _normalize_friday_pronunciation(text)
    text = _strip_speech_template_markers(text)
    text = re.sub(r"\ba\.?k\.?a\b\.?", "also known as", text, flags=re.IGNORECASE)

    text = re.sub(r"\*+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"^\s*[\-\*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "").replace("#", "")
    text = re.sub(r"https?://\S+", "", text)
    if "{" in text and "}" in text:
        text = re.sub(r"\{[^\}]+\}", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Skip expensive number/date normalization when there are no digits
    if re.search(r"\d", text):
        text = normalize_numbers_for_speech(text)

    return text.strip()


def _ensure_model_loaded():
    global _model, _voice_state
    if _model is not None and _voice_state is not None:
        return

    with _model_lock:
        if _model is not None and _voice_state is not None:
            return

        _require_cloning_weights()
        model = TTSModel.load_model(
            temp=_LOAD_TEMP,
            sampler_decode_steps=_LOAD_LSD_STEPS,
            noise_clamp=_LOAD_NOISE_CLAMP,
            eos_threshold=_LOAD_EOS_THRESHOLD,
        )
        if not getattr(model, "has_voice_cloning", True):
            raise RuntimeError("pocket_tts voice cloning is unavailable in this install.")

        wav_path = _resolve_voice_wav_path()
        voice_state = _load_friday_voice_state(model, wav_path)
        _model = model
        _voice_state = voice_state
        print(
            f"[TTS] FRIDAY voice ready — cloned from {wav_path.name} "
            f"(temp={_LOAD_TEMP}, steps={_LOAD_LSD_STEPS})"
        )


def _voice_state_for_generation(*, force_reload: bool = False):
    """Return in-memory voice state; re-encode from WAV only when missing or forced."""
    global _voice_state
    _ensure_model_loaded()
    if _voice_state is None or force_reload:
        wav_path = _resolve_voice_wav_path()
        _voice_state = _load_friday_voice_state(_model, wav_path)
    return _voice_state


_TTS_CHUNK_MAX = 200
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")


def _split_for_tts(text: str, max_chars: int = _TTS_CHUNK_MAX) -> list[str]:
    """Break long replies into natural sentence-sized pieces without cutting words."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    for sentence in sentences:
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip()]
        cur = ""
        for clause in clauses:
            if len(clause) > max_chars:
                words = clause.split()
                w_buf = ""
                for w in words:
                    if len(w_buf) + len(w) + 1 <= max_chars:
                        w_buf = f"{w_buf} {w}".strip()
                    else:
                        if w_buf:
                            chunks.append(w_buf)
                        w_buf = w
                if w_buf:
                    chunks.append(w_buf)
            elif len(cur) + len(clause) + 2 <= max_chars:
                cur = f"{cur}, {clause}".strip(", ")
            else:
                if cur:
                    chunks.append(cur)
                cur = clause
        if cur:
            chunks.append(cur)

    return [c for c in chunks if len(c) >= 2] or [text]


def warm_up_tts():
    """Load the model and voice state ahead of the first spoken response."""
    try:
        _ensure_model_loaded()
    except Exception as exc:
        print(f"[TTS Warmup Error] {exc}")


def _chunk_to_samples(chunk) -> np.ndarray:
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    samples = np.asarray(chunk, dtype=np.float32).reshape(-1, 1)
    return np.clip(samples, -1.0, 1.0)


def _raw_audio_to_samples(raw, target_rate: int) -> np.ndarray | None:
    if hasattr(raw, "detach"):
        raw = raw.detach().cpu().numpy()
    samples = np.asarray(raw, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.reshape(-1, 1)
    samples = np.clip(samples, -1.0, 1.0)
    samples = _resample_audio(samples, _model.sample_rate, target_rate)
    return samples if len(samples) > 0 else None


def _ensure_playback_stream(target_rate: int):
    """Reuse a single low-latency output stream across speak() calls."""
    global _playback_stream
    if _playback_stream is not None:
        try:
            if _playback_stream.active:
                return _playback_stream
        except Exception:
            pass
        try:
            _playback_stream.close()
        except Exception:
            pass
    stream = sd.OutputStream(
        samplerate=target_rate,
        channels=1,
        dtype="float32",
        latency="low",
        blocksize=128,
    )
    stream.start()
    _playback_stream = stream
    return stream


_MIN_AUDIBLE_SECONDS = 0.12


def _play_samples(samples: np.ndarray, target_rate: int) -> bool:
    """Play audio through a persistent stream for gapless multi-chunk playback."""
    audio = np.asarray(samples, dtype=np.float32).flatten()
    duration = len(audio) / float(target_rate)
    if duration < _MIN_AUDIBLE_SECONDS:
        print(f"[TTS] Skipping inaudible clip ({duration:.2f}s)")
        return False

    ok, reason = _audio_is_safe(audio, target_rate)
    if not ok:
        print(f"[TTS] Skipping unsafe clip ({reason})")
        return False

    fade = max(1, int(target_rate * 0.008))
    if audio.size > 2 * fade:
        audio = audio.copy()
        audio[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

    print(f"[TTS] Playing {duration:.2f}s ({reason}) @ {target_rate}Hz")
    import sys
    import time as _time

    def _play_blocking() -> bool:
        play_data = np.column_stack([audio, audio]) if audio.ndim == 1 else audio
        sd.play(play_data, samplerate=target_rate, blocking=True)
        return True

    # macOS CoreAudio: sounddevice / PortAudio clashes with the active STT
    # microphone stream, causing severe buffer underruns, stutter, and broken robotic audio.
    # afplay plays directly via macOS system AudioServices with hardware resampling and zero underrun.
    if sys.platform == "darwin":
        global _afplay_proc
        import tempfile
        import subprocess
        import scipy.io.wavfile as _wavfile

        tmp_wav = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_wav = f.name
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            _wavfile.write(tmp_wav, target_rate, pcm)

            _afplay_proc = subprocess.Popen(["afplay", tmp_wav])
            while _afplay_proc.poll() is None:
                if _stop_event.is_set():
                    _afplay_proc.terminate()
                    try:
                        _afplay_proc.kill()
                    except Exception:
                        pass
                    break
                _time.sleep(0.01)
            _afplay_proc = None
            return True
        except Exception as exc:
            print(f"[TTS] afplay failed ({exc}), falling back to sounddevice")
            _afplay_proc = None
            try:
                return _play_blocking()
            except Exception as exc2:
                print(f"[TTS] sounddevice playback failed ({exc2})")
                return False
        finally:
            if tmp_wav:
                try:
                    os.unlink(tmp_wav)
                except Exception:
                    pass

    idx = 0
    n = int(audio.size)
    deadline = _time.monotonic() + duration + 1.5

    def _cb(outdata, frames, _time_info, status):
        nonlocal idx
        if status:
            print(f"[TTS] stream status: {status}")
        if _stop_event.is_set() or idx >= n:
            outdata.fill(0)
            raise sd.CallbackStop
        end = min(n, idx + frames)
        chunk = audio[idx:end]
        outdata[: len(chunk), 0] = chunk
        if len(chunk) < frames:
            outdata[len(chunk) :, 0] = 0
        idx = end
        if idx >= n:
            raise sd.CallbackStop

    try:
        with sd.OutputStream(
            samplerate=target_rate,
            channels=1,
            dtype="float32",
            callback=_cb,
            blocksize=2048,
            latency="high",
        ):
            while idx < n and not _stop_event.is_set() and _time.monotonic() < deadline:
                sd.sleep(20)
        return idx >= n
    except sd.CallbackStop:
        return idx >= n
    except Exception as exc:
        print(f"[TTS] OutputStream failed ({exc}); falling back to sd.play")
        _safe_sd_stop()
        try:
            return _play_blocking()
        except Exception as play_exc:
            print(f"[TTS] sd.play failed ({play_exc})")
            return False


def _set_tts_spoke(ok: bool) -> None:
    try:
        from services.runtime_state import flags

        flags.tts_spoke_this_turn = ok
    except Exception:
        pass


def speak(text: str) -> bool:
    """Generate each sentence fully, then play it as one clip."""
    global _is_speaking, _stream_active

    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 2:
        _set_tts_spoke(False)
        return False

    try:
        _ensure_model_loaded()
    except Exception as exc:
        print(f"[TTS Load Error] {exc}")
        _set_tts_spoke(False)
        return False

    if _is_speaking or _stream_active:
        stop_speech()

    chunks = _split_for_tts(clean_text)
    if not chunks:
        _set_tts_spoke(False)
        return False

    if not _playback_lock.acquire(timeout=8.0):
        _set_tts_spoke(False)
        return False

    try:
        _stop_event.clear()
        _is_speaking = True
        _stream_active = True
        _duplex_notify_start(clean_text)
        try:
            from services.tts_broadcast import notify_tts_active

            notify_tts_active(True)
        except Exception:
            pass
        _safe_sd_stop()

        target_rate = _get_hardware_sample_rate()
        audio_pieces: list[np.ndarray] = []
        silence = np.zeros(int(target_rate * 0.12), dtype=np.float32)

        for i, sentence in enumerate(chunks):
            if _stop_event.is_set():
                break
            samples = _generate_audio_for_text(sentence)
            if samples is None or samples.size == 0:
                continue
            audio_pieces.append(samples.flatten())
            if i < len(chunks) - 1:
                audio_pieces.append(silence)

        if not audio_pieces or _stop_event.is_set():
            _set_tts_spoke(False)
            return False

        full_audio = np.concatenate(audio_pieces)
        fade = min(int(target_rate * 0.006), full_audio.size // 4)
        if fade > 0:
            full_audio[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            full_audio[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

        ok = _play_samples(full_audio, target_rate)
        _set_tts_spoke(ok)
        return ok
    except Exception as exc:
        print(f"[TTS Speak Error] {exc}")
        _set_tts_spoke(False)
        return False
    finally:
        _is_speaking = False
        _stream_active = False
        _stop_event.clear()
        _duplex_notify_end()
        _playback_lock.release()
        try:
            from services.tts_broadcast import notify_tts_active

            notify_tts_active(False)
        except Exception:
            pass


# ── Streaming audio pipeline ────────────────────────────────────────────────────
# Producer-consumer for sentence-buffered token-to-audio streaming.
# _stream_sentence_queue holds sentences; _stream_thread generates audio
# and plays it through a persistent OutputStream.

_stream_sentence_queue: queue.Queue = queue.Queue()
_stream_thread: threading.Thread | None = None
_stream_active = False
_stream_sentences_played = 0


def _generate_audio_for_text(text: str) -> np.ndarray | None:
    """Generate audio samples for a single text chunk with serialized model access."""
    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 2:
        return None
    try:
        voice_state = _voice_state_for_generation()
    except Exception:
        return None
    target_rate = _get_hardware_sample_rate()
    model_rate = getattr(_model, "sample_rate", 24000)

    with _model_lock:
        try:
            if _stop_event.is_set():
                return None
            audio_tensor = _model.generate_audio(
                model_state=voice_state,
                text_to_generate=clean_text,
                copy_state=True,
            )
            if audio_tensor is None or audio_tensor.numel() == 0 or _stop_event.is_set():
                return None
            raw = audio_tensor.detach().cpu().numpy().reshape(-1)
            polished = _postprocess(raw, model_rate)
            ok, reason = _audio_is_safe(polished, model_rate)
            if not ok:
                print(f"[TTS] Generated audio rejected ({reason})")
                return None
            return _raw_audio_to_samples(polished, target_rate)
        except Exception as exc:
            print(f"[TTS Gen Error] {exc}")
            return None


def _stream_worker():
    """Background thread: consumes sentences and plays smoothly without thread collisions."""
    global _active_stream, _is_speaking, _stream_sentences_played
    target_rate = _get_hardware_sample_rate()

    try:
        while not _stop_event.is_set():
            try:
                sentence = _stream_sentence_queue.get(timeout=0.25)
            except queue.Empty:
                if not _stream_active:
                    break
                continue

            if sentence is None or _stop_event.is_set():
                break

            samples = _generate_audio_for_text(sentence)
            if samples is not None and not _stop_event.is_set():
                if not _playback_lock.acquire(timeout=4.0):
                    continue
                try:
                    _is_speaking = True
                    if _play_samples(samples, target_rate):
                        _stream_sentences_played += 1
                finally:
                    _is_speaking = False
                    _playback_lock.release()
    except Exception as exc:
        print(f"[TTS Stream Error] {exc}")
    finally:
        _is_speaking = False
        _active_stream = None
        _duplex_notify_end()
        try:
            from services.tts_broadcast import notify_tts_active

            notify_tts_active(False)
        except Exception:
            pass


def is_streaming() -> bool:
    return _stream_active


def start_streaming():
    """Start the streaming TTS pipeline."""
    global _stream_active, _stream_thread, _stream_sentences_played
    warm_up_tts()
    _stream_sentences_played = 0
    _stream_active = True
    _duplex_notify_start("")  # mark TTS active for duplex gate (text appended per sentence)
    try:
        from services.tts_broadcast import notify_tts_active

        notify_tts_active(True)
    except Exception:
        pass
    _stop_event.clear()
    _safe_sd_stop()
    # Drain any stale sentences
    while not _stream_sentence_queue.empty():
        try:
            _stream_sentence_queue.get_nowait()
        except queue.Empty:
            break
    _stream_thread = threading.Thread(target=_stream_worker, daemon=True)
    _stream_thread.start()


def stream_sentence(text: str):
    """Push a sentence into the streaming TTS pipeline."""
    # Record for echo filter even before audio generates
    if text and len(text.strip()) >= 2:
        _duplex_notify_start(text.strip())
    _stream_sentence_queue.put(text)


def stop_streaming(wait_timeout: float = 120.0) -> int:
    """Stop the streaming pipeline after queued sentences finish playing."""
    global _stream_active, _stream_thread
    _stream_active = False
    _stream_sentence_queue.put(None)  # sentinel
    thread = _stream_thread
    _stream_thread = None
    if thread is not None:
        thread.join(timeout=wait_timeout)
        if thread.is_alive():
            print(f"[TTS] Streaming worker still running after {wait_timeout}s")
    return _stream_sentences_played


def stream_sentences_played() -> int:
    return _stream_sentences_played


def stop_speech():
    """Stop any current Pocket TTS playback as quickly as possible."""
    global _active_stream, _is_speaking, _stream_active, _stream_thread, _playback_stream, _afplay_proc

    was_speaking = is_tts_active()
    _stop_event.set()
    _stream_active = False

    if _afplay_proc is not None:
        try:
            _afplay_proc.terminate()
            _afplay_proc.kill()
        except Exception:
            pass
        _afplay_proc = None

    while not _stream_sentence_queue.empty():
        try:
            _stream_sentence_queue.get_nowait()
        except queue.Empty:
            break
    try:
        _stream_sentence_queue.put_nowait(None)
    except queue.Full:
        pass

    try:
        if _active_stream is not None:
            _active_stream.abort()
            _active_stream.stop()
            _active_stream.close()
    except Exception as exc:
        print(f"[TTS Stop Error] {exc}")
    _active_stream = None

    thread = _stream_thread
    _stream_thread = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    while not _stream_sentence_queue.empty():
        try:
            _stream_sentence_queue.get_nowait()
        except queue.Empty:
            break

    try:
        import pygame

        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
    except Exception:
        pass

    if _playback_stream is not None:
        try:
            _playback_stream.abort()
            _playback_stream.stop()
            _playback_stream.close()
        except Exception:
            pass
        _playback_stream = None

    _is_speaking = False
    _stop_event.clear()
    if was_speaking:
        _duplex_notify_end()
        try:
            from services.tts_broadcast import notify_tts_active

            notify_tts_active(False)
        except Exception:
            pass


def is_speaking() -> bool:
    return _is_speaking


def is_tts_active() -> bool:
    """True when audio is generating or playing."""
    if _is_speaking or _stream_active:
        return True
    if _stream_thread is not None and _stream_thread.is_alive():
        return True
    if _afplay_proc is not None and _afplay_proc.poll() is None:
        return True
    return False


def speak_filler(text: str):
    """
    High-priority short utterance that interrupts current speech.
    Use for latency-hiding filler phrases like 'Looking it up, boss.'
    Generates only a few words so it completes quickly.
    """
    global _is_speaking, _active_stream

    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 2:
        return

    try:
        _ensure_model_loaded()
    except Exception as exc:
        print(f"[TTS Filler Error] {exc}")
        return

    if _is_speaking or _stream_active:
        stop_speech()

    if not _playback_lock.acquire(timeout=0.3):
        return

    try:
        _stop_event.clear()
        _is_speaking = True
        _duplex_notify_start(clean_text)
        target_rate = _get_hardware_sample_rate()

        samples = _generate_audio_for_text(clean_text)
        if samples is None or _stop_event.is_set():
            return
        _play_samples(samples, target_rate)
    except Exception as exc:
        print(f"[TTS Filler Error] {exc}")
    finally:
        _active_stream = None
        _is_speaking = False
        _duplex_notify_end()
        _stop_event.clear()
        _playback_lock.release()
