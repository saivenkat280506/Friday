"""
filter.py — Reject phantom STT results (Whisper hallucinations, echo, filler).

Phase 0 enhancement: integrates duplex echo filter (fuzzy match against last
1-2 TTS utterances) instead of exact word-boundary only.
"""
import re
from difflib import SequenceMatcher

try:
    from stt.duplex import duplex as _duplex
except Exception:
    _duplex = None  # type: ignore

_PHANTOM_EXACT = {
    "thank you", "thank you.", "thanks", "thanks.", "you", "the", "bye",
    "okay", "ok", "uh", "um", "hmm", "ah", "oh", "huh", "yeah", "yes", "no",
    "thank you for watching", "thanks for watching", "subscribe",
    "you're welcome", "youre welcome",
    "subtitles by the amara.org community",
    "subtitles by the amara org community",
    "give me a minute", "give me a second", "give me one minute",
    "just a minute", "just a second", "just a moment",
    "one minute", "one moment", "one second",
    "hold on", "hold on a minute", "hold on a second",
    "wait a minute", "wait a second", "wait a moment",
    "that means", "that means give me a minute",
    "that means, give me a minute",
}

# Whisper training-data hallucinations (YouTube credits, websites, etc.)
_HALLUCINATION_MARKERS = (
    "amara.org",
    "amara org",
    "subtitles by",
    "subtitle by",
    "non-profit organization",
    "nonprofit organization",
    "community-driven model",
    "community driven model",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "visit www",
    "http://",
    "https://",
    "mbc news",
    "copyright",
    "all rights reserved",
)

_PHANTOM_PATTERNS = (
    r"^thank\s+you\b",
    r"^thanks\b",
    r"^subscribe\b",
    r"^for\s+watching\b",
    r"^subtitles?\s+by\b",
    r"\bamara\.?org\b",
    r"^that means\b",
    r"^give me a (?:minute|second|moment)\b",
    r"^just a (?:minute|second|moment)\b",
    r"^wait a (?:minute|second|moment)\b",
)


def is_whisper_hallucination(text: str) -> bool:
    """Detect known Whisper garbage (subtitle credits, websites, etc.)."""
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _HALLUCINATION_MARKERS):
        return True
    for pattern in _PHANTOM_PATTERNS:
        if re.search(pattern, lowered):
            return True
    return False


def _normalize_for_echo(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _is_fuzzy_echo(cleaned: str, assistant: str, threshold: float = 0.82) -> bool:
    """Fuzzy echo detection — handles Whisper rephrasing of TTS output."""
    if not cleaned or not assistant:
        return False
    a_norm = _normalize_for_echo(cleaned)
    b_norm = _normalize_for_echo(assistant)
    if len(a_norm) < 4 or len(b_norm) < 4:
        return False
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        return True
    if SequenceMatcher(None, a_norm, b_norm).ratio() >= threshold:
        return True
    # token Jaccard fallback
    ta, tb = set(a_norm.split()), set(b_norm.split())
    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.80:
        return True
    return False


def is_phantom_transcript(text: str, *, last_assistant: str = "") -> bool:
    """Return True if this transcript should be ignored (not a real user command)."""
    if not text:
        return True

    cleaned = re.sub(r"\s+", " ", text.lower().strip()).rstrip(".!?,")
    if len(cleaned) < 2:
        return True
    if cleaned in _PHANTOM_EXACT:
        return True
    if is_whisper_hallucination(cleaned):
        return True

    # ── Echo check — duplex fuzzy history first ──────────────────────────
    if _duplex is not None:
        try:
            is_echo, _ = _duplex.is_echo(text)
            if is_echo:
                # barge-in phrases ("stop", "friday", "wait") are not phantom
                if not _duplex.is_barge_in(text):
                    return True
        except Exception:
            pass
        # also check explicit last_assistant passed by caller (may differ from duplex history)
        if last_assistant and _is_fuzzy_echo(cleaned, last_assistant):
            # allow barge-in through
            try:
                if _duplex.is_barge_in(text):
                    return False
            except Exception:
                pass
            return True
        # if caller passed no last_assistant, duplex history already covered
        if last_assistant:
            # legacy exact word-boundary fallback (kept for compatibility)
            assistant = last_assistant.lower()
            if re.search(rf"\b{re.escape(cleaned)}\b", assistant):
                return True
        return False

    # ── Fallback when duplex not available (tests / older paths) ─────────
    if last_assistant:
        if _is_fuzzy_echo(cleaned, last_assistant):
            return True
        assistant = last_assistant.lower()
        if re.search(rf"\b{re.escape(cleaned)}\b", assistant):
            return True

    return False


def should_drop_transcript(text: str, *, last_assistant: str = "") -> tuple[bool, str]:
    """
    Unified drop check wrapping is_phantom_transcript + duplex tail/TTS gating.

    Returns (drop, reason) — reason is one of:
      "ok", "phantom", "hallucination", "echo", "tts_active", "tail"
    """
    if _duplex is not None:
        try:
            return _duplex.should_drop_transcript(text)
        except Exception:
            pass
    if is_phantom_transcript(text, last_assistant=last_assistant):
        if is_whisper_hallucination(text):
            return True, "hallucination"
        return True, "phantom"
    return False, "ok"