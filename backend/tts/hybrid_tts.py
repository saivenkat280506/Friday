"""
hybrid_tts.py — FRIDAY Voice Controller (Pocket TTS only)
==========================================================
All speech uses local pocket_tts with voice cloning from friday-voice.wav.
No cloud APIs (Groq/ElevenLabs) and no edge-tts fallback.
"""

import asyncio
import re
import threading

_is_speaking = False
_current_response_id = None


async def speak_hybrid(text: str, is_smart: bool = False, response_id: str = None) -> bool:
    global _is_speaking, _current_response_id

    clean_text = text.strip() if text else ""
    if not clean_text or len(clean_text) < 2 or clean_text in ["...", "."]:
        print(f"[TTS] Skipping empty/short text: {clean_text!r}")
        return False

    if _is_speaking:
        return False

    if response_id and response_id == _current_response_id:
        return False

    from brain.settings import is_muted
    if is_muted():
        print("[TTS] Skipped — voice is muted in settings")
        return False

    try:
        _is_speaking = True
        _current_response_id = response_id
        print(f"[TTS] Speaking {len(clean_text)} chars via pocket_tts: {clean_text[:80]!r}...")

        from tts.pocket_tts import speak as pocket_speak

        ok = await asyncio.to_thread(pocket_speak, clean_text)
        if ok:
            print("[TTS] pocket_tts succeeded")
            return True
        print("[TTS] pocket_tts failed — no fallback (pocket-only mode)")
        _current_response_id = None
        return False
    except Exception as exc:
        print(f"[TTS] pocket_tts error: {exc}")
        _current_response_id = None
        return False
    finally:
        _is_speaking = False


_filler_thread_pool = set()


def speak_filler(filler_text: str):
    """Fire-and-forget filler TTS. Never blocks the caller."""
    if not filler_text or len(filler_text.strip()) < 2:
        return
    from brain.settings import is_muted
    if is_muted():
        return

    def _play():
        try:
            from tts.pocket_tts import speak_filler as pocket_filler
            pocket_filler(filler_text.strip())
        except Exception:
            pass

    t = threading.Thread(target=_play, daemon=True)
    t.start()
    _filler_thread_pool.add(t)
    done = {t for t in _filler_thread_pool if not t.is_alive()}
    _filler_thread_pool.difference_update(done)


def start_audio_stream():
    """Start the sentence-buffered streaming audio pipeline."""
    try:
        from tts.pocket_tts import start_streaming
        start_streaming()
    except Exception as e:
        print(f"[TTS] start_streaming failed: {e}")


def stream_sentence(sentence: str):
    """Push a sentence into the streaming TTS pipeline."""
    try:
        from tts.pocket_tts import stream_sentence as pocket_stream
        pocket_stream(sentence)
    except Exception as e:
        print(f"[TTS] stream_sentence failed: {e}")


def stop_audio_stream(wait_timeout: float = 120.0) -> int:
    """Stop the streaming audio pipeline and wait for playback to finish."""
    try:
        from tts.pocket_tts import stop_streaming
        played = stop_streaming(wait_timeout=wait_timeout)
        print(f"[TTS] Streaming finished — {played} sentence(s) played.")
        return played
    except Exception as e:
        print(f"[TTS] stop_streaming failed: {e}")
        return 0


class StreamingTtsBuffer:
    """Feed LLM token chunks into sentence-sized streaming TTS for low latency."""

    # Split only on real sentence endings — not ellipses (they cause choppy gaps).
    _SENTENCE_END = re.compile(r'(?<![.])[.!?]+[\s"\')]*')
    _BATCH_CHAR_LIMIT = 120

    def __init__(self, on_first_sentence=None) -> None:
        self._buffer = ""
        self._spoken = ""
        self._active = False
        self._stop_thread: threading.Thread | None = None
        self._on_first_sentence = on_first_sentence
        self._first_sentence_fired = False
        self._batch_pending: list[str] = []
        self._batch_chars = 0

    def start(self) -> None:
        if self._active:
            return
        from brain.settings import is_muted

        if is_muted():
            return
        start_audio_stream()
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def feed(self, chunk: str) -> None:
        if not self._active or not chunk:
            return
        self._buffer += chunk
        self._emit_complete_sentences()

    def _sanitize_clause(self, text: str) -> str:
        from tts.pocket_tts import clean_text_for_speech

        return clean_text_for_speech(text)

    def _fire_first_sentence_hook(self) -> None:
        if self._first_sentence_fired or not self._on_first_sentence:
            return
        self._first_sentence_fired = True
        try:
            self._on_first_sentence()
        except Exception:
            pass

    def _flush_batch(self, *, force: bool = False) -> None:
        if not self._batch_pending:
            return
        if not force and self._batch_chars < self._BATCH_CHAR_LIMIT:
            return
        batch = " ".join(self._batch_pending).strip()
        self._batch_pending.clear()
        self._batch_chars = 0
        if len(batch) >= 2:
            stream_sentence(batch)
            self._spoken += batch + " "

    def _queue_for_tts(self, sentence: str) -> None:
        if len(sentence) < 3:
            return
        if not self._first_sentence_fired:
            self._fire_first_sentence_hook()
            stream_sentence(sentence)
            self._spoken += sentence + " "
            return

        self._batch_pending.append(sentence)
        self._batch_chars += len(sentence)
        if self._batch_chars >= self._BATCH_CHAR_LIMIT or len(self._batch_pending) >= 2:
            self._flush_batch(force=True)

    def _emit_complete_sentences(self) -> None:
        while True:
            match = self._SENTENCE_END.search(self._buffer)
            if not match:
                break
            end = match.end()
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:].lstrip()
            sentence = self._sanitize_clause(sentence)
            if len(sentence) >= 3:
                self._queue_for_tts(sentence)

    def cancel(self) -> None:
        """Abort buffered streaming TTS immediately (e.g. app closed mid-response)."""
        self._buffer = ""
        self._spoken = ""
        self._active = False
        try:
            from tts.pocket_tts import stop_speech

            stop_speech()
        except Exception:
            pass

    def finish(self) -> None:
        if not self._active:
            return
        from services.runtime_state import flags, stop_event

        if flags.stop_listen_trigger or stop_event.is_set():
            self.cancel()
            return
        remaining = (self._buffer or "").strip()
        self._buffer = ""
        if remaining:
            clause = self._sanitize_clause(remaining)
            if len(clause) >= 2:
                if not self._first_sentence_fired:
                    self._fire_first_sentence_hook()
                    stream_sentence(clause)
                    self._spoken += clause + " "
                else:
                    self._batch_pending.append(clause)
                    self._batch_chars += len(clause)
        self._flush_batch(force=True)
        self._spoken = ""
        self._active = False

        def _stop() -> None:
            stop_audio_stream(wait_timeout=180.0)

        self._stop_thread = threading.Thread(target=_stop, daemon=True)
        self._stop_thread.start()


async def wait_for_streaming_tts_idle(timeout_s: float = 180.0) -> None:
    """Block until streaming TTS playback has fully finished."""
    from tts.pocket_tts import is_speaking, is_streaming

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not is_speaking() and not is_streaming():
            return
        await asyncio.sleep(0.05)