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
_FRAMES_AFTER_EOS = 1  # lower = faster synthesis; 1 is enough for clean endings

_model = None
_voice_state = None
_hardware_sample_rate = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False
_active_stream = None
_playback_stream = None


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

    path = _DEFAULT_VOICE_WAV.resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"FRIDAY voice file missing: {path}. "
            "Place friday-voice.wav in backend/tts/voices/ or set FRIDAY_VOICE_PATH."
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
    return model.get_state_for_audio_prompt(wav_path, truncate=False)


def _get_hardware_sample_rate():
    global _hardware_sample_rate
    if _hardware_sample_rate is not None:
        return _hardware_sample_rate
    try:
        device_info = sd.query_devices(kind='output')
        _hardware_sample_rate = int(device_info['default_samplerate'])
    except Exception:
        _hardware_sample_rate = 44100
    return _hardware_sample_rate

def _resample_audio(audio, original_rate, target_rate):
    if original_rate == target_rate:
        return audio
    duration = len(audio) / original_rate
    num_samples = int(duration * target_rate)
    return np.interp(
        np.linspace(0, len(audio), num_samples, endpoint=False),
        np.arange(len(audio)),
        audio.flatten()
    ).reshape(-1, 1).astype(np.float32)


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

        model = TTSModel.load_model()
        if not model.has_voice_cloning:
            raise RuntimeError("pocket_tts voice cloning is unavailable in this install.")

        wav_path = _resolve_voice_wav_path()
        voice_state = _load_friday_voice_state(model, wav_path)
        _model = model
        _voice_state = voice_state
        print(f"[TTS] FRIDAY voice ready — cloned from {wav_path.name}")


def _voice_state_for_generation(*, force_reload: bool = False):
    """Return in-memory voice state; re-encode from WAV only when missing or forced."""
    global _voice_state
    _ensure_model_loaded()
    if _voice_state is None or force_reload:
        wav_path = _resolve_voice_wav_path()
        _voice_state = _load_friday_voice_state(_model, wav_path)
    return _voice_state


_TTS_CHUNK_MAX = 140
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_for_tts(text: str, max_chars: int = _TTS_CHUNK_MAX) -> list[str]:
    """Break long replies into pocket-tts-safe chunks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf = ""
    for part in _SENTENCE_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        candidate = f"{buf} {part}".strip() if buf else part
        if len(candidate) <= max_chars:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(part) <= max_chars:
            buf = part
            continue
        for i in range(0, len(part), max_chars):
            piece = part[i : i + max_chars].strip()
            if len(piece) >= 2:
                chunks.append(piece)
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= 2] or [text[:max_chars].strip()]


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


_MIN_AUDIBLE_SECONDS = 0.25


def _play_samples(samples: np.ndarray, target_rate: int) -> bool:
    """Play audio through a persistent stream for gapless multi-chunk playback."""
    audio = np.asarray(samples, dtype=np.float32).flatten()
    duration = len(audio) / float(target_rate)
    if duration < _MIN_AUDIBLE_SECONDS:
        print(f"[TTS] Skipping inaudible clip ({duration:.2f}s)")
        return False

    print(f"[TTS] Playing {duration:.2f}s on device {sd.default.device[1]} @ {target_rate}Hz")
    try:
        stream = _ensure_playback_stream(target_rate)
        block = 1024
        for start in range(0, len(audio), block):
            if _stop_event.is_set():
                return False
            chunk = audio[start : start + block]
            stream.write(chunk.reshape(-1, 1))
        return True
    except Exception as exc:
        print(f"[TTS] stream playback failed ({exc}), trying sd.play")
        try:
            sd.play(audio, target_rate)
            sd.wait()
            return True
        except Exception as exc2:
            print(f"[TTS] sounddevice playback failed ({exc2}), trying pygame")
            return _play_samples_pygame(audio, target_rate)


def _play_samples_pygame(audio: np.ndarray, target_rate: int) -> bool:
    import tempfile
    import wave

    try:
        import pygame
    except Exception as exc:
        print(f"[TTS] pygame unavailable: {exc}")
        return False

    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(target_rate)
            wf.writeframes(pcm16.tobytes())

        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=target_rate, size=-16, channels=1, buffer=512)

        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(50)
        pygame.mixer.music.unload()
        return True
    except Exception as exc:
        print(f"[TTS] pygame playback failed: {exc}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _set_tts_spoke(ok: bool) -> None:
    try:
        from services.runtime_state import flags

        flags.tts_spoke_this_turn = ok
    except Exception:
        pass


def speak(text: str) -> bool:
    """Generate speech with sentence prefetching to avoid gaps between chunks."""
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

    try:
        _stop_event.clear()
        start_streaming()
        for chunk in chunks:
            if _stop_event.is_set():
                break
            stream_sentence(chunk)
        played = stop_streaming(wait_timeout=180.0)
        ok = played > 0
        _set_tts_spoke(ok)
        return ok
    except Exception as exc:
        print(f"[TTS Speak Error] {exc}")
        _set_tts_spoke(False)
        return False


# ── Streaming audio pipeline ────────────────────────────────────────────────────
# Producer-consumer for sentence-buffered token-to-audio streaming.
# _stream_sentence_queue holds sentences; _stream_thread generates audio
# and plays it through a persistent OutputStream.

_stream_sentence_queue: queue.Queue = queue.Queue()
_stream_thread: threading.Thread | None = None
_stream_active = False
_stream_sentences_played = 0


def _generate_audio_for_text(text: str) -> np.ndarray | None:
    """Generate audio samples for a single text chunk."""
    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 2:
        return None
    try:
        voice_state = _voice_state_for_generation()
    except Exception:
        return None
    target_rate = _get_hardware_sample_rate()
    try:
        raw = _model.generate_audio(
            model_state=voice_state,
            text_to_generate=clean_text,
            frames_after_eos=_FRAMES_AFTER_EOS,
            copy_state=True,
        )
        return _raw_audio_to_samples(raw, target_rate)
    except Exception as exc:
        print(f"[TTS Gen Error] {exc}")
        return None


def _stream_worker():
    """Background thread: consumes sentences, prefetches audio, plays in real-time."""
    global _active_stream, _is_speaking, _stream_sentences_played
    target_rate = _get_hardware_sample_rate()

    prefetch_lock = threading.Lock()
    prefetch_box: dict[str, Any] = {"sentence": None, "samples": None}
    prefetch_thread: threading.Thread | None = None
    held_sentence: str | None = None

    def _prefetch(sentence: str) -> None:
        samples = _generate_audio_for_text(sentence)
        with prefetch_lock:
            prefetch_box["sentence"] = sentence
            prefetch_box["samples"] = samples

    def _take_prefetched(sentence: str) -> np.ndarray | None:
        with prefetch_lock:
            if prefetch_box["sentence"] == sentence and prefetch_box["samples"] is not None:
                samples = prefetch_box["samples"]
                prefetch_box["sentence"] = None
                prefetch_box["samples"] = None
                return samples
        return None

    def _next_sentence() -> str | None:
        nonlocal held_sentence
        if held_sentence is not None:
            sentence = held_sentence
            held_sentence = None
            return sentence
        try:
            return _stream_sentence_queue.get(timeout=0.3)
        except queue.Empty:
            return "__WAIT__"

    try:
        while True:
            if _stop_event.is_set():
                break

            sentence = _next_sentence()
            if sentence == "__WAIT__":
                if not _stream_active:
                    break
                continue
            if sentence is None:
                break
            if _stop_event.is_set():
                break

            samples = _take_prefetched(sentence)
            if samples is None:
                samples = _generate_audio_for_text(sentence)

            # Prefetch the following sentence while this one plays.
            try:
                upcoming = _stream_sentence_queue.get_nowait()
            except queue.Empty:
                upcoming = None
            if upcoming is not None and upcoming is not sentence:
                held_sentence = upcoming
                if prefetch_thread is None or not prefetch_thread.is_alive():
                    prefetch_thread = threading.Thread(
                        target=_prefetch, args=(upcoming,), daemon=True
                    )
                    prefetch_thread.start()

            if samples is not None and not _stop_event.is_set():
                if not _playback_lock.acquire(timeout=2.0):
                    continue
                try:
                    _is_speaking = True
                    if _play_samples(samples, target_rate):
                        _stream_sentences_played += 1
                finally:
                    _is_speaking = False
                    _playback_lock.release()

            if prefetch_thread is not None and prefetch_thread.is_alive():
                prefetch_thread.join(timeout=0.05)

    except Exception as exc:
        print(f"[TTS Stream Error] {exc}")
    finally:
        _is_speaking = False
        _active_stream = None
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
    try:
        from services.tts_broadcast import notify_tts_active

        notify_tts_active(True)
    except Exception:
        pass
    _stop_event.clear()
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
    _stream_sentence_queue.put(text)


def stop_streaming(wait_timeout: float = 120.0) -> int:
    """Stop the streaming pipeline after queued sentences finish playing."""
    global _stream_active, _stream_thread
    _stream_active = False
    _stream_sentence_queue.put(None)  # sentinel
    if _stream_thread is not None:
        _stream_thread.join(timeout=wait_timeout)
        if _stream_thread.is_alive():
            print(f"[TTS] Streaming worker still running after {wait_timeout}s")
        _stream_thread = None
    return _stream_sentences_played


def stream_sentences_played() -> int:
    return _stream_sentences_played


def stop_speech():
    """Stop any current Pocket TTS playback as quickly as possible."""
    global _active_stream, _is_speaking, _stream_active, _stream_thread, _playback_stream

    was_speaking = is_tts_active()
    _stop_event.set()
    _stream_active = False

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

    if _stream_thread is not None and _stream_thread.is_alive():
        _stream_thread.join(timeout=2.0)
    _stream_thread = None
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
        target_rate = _get_hardware_sample_rate()

        voice_state = _voice_state_for_generation()
        raw = _model.generate_audio(
            model_state=voice_state,
            text_to_generate=clean_text,
            frames_after_eos=1,
            copy_state=True,
        )
        if _stop_event.is_set():
            return

        samples = _raw_audio_to_samples(raw, target_rate)
        if samples is None:
            return

        _play_samples(samples, target_rate)
    except Exception as exc:
        print(f"[TTS Filler Error] {exc}")
    finally:
        _active_stream = None
        _is_speaking = False
        _stop_event.clear()
        _playback_lock.release()
