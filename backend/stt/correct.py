"""
correct.py — Post-process STT output to fix common mishearings.
Only applied to user speech input, never to FRIDAY responses.
"""
import re
from difflib import get_close_matches

# Exact phrase fixes (regex -> replacement)
_PHRASE_FIXES: list[tuple[str, str]] = [
    (r"\bplace\s+music\b", "play music"),
    (r"\bplayed\s+music\b", "play music"),
    (r"\bpray\s+music\b", "play music"),
    (r"\bplay\s+some\s+music\b", "play music"),
    (r"\bstart\s+music\b", "play music"),
    (r"\bstop\s+the\s+music\b", "stop music"),
    (r"\bpause\s+the\s+music\b", "pause music"),
    (r"\bresume\s+the\s+music\b", "resume music"),
    (r"\bunpause\s+music\b", "resume music"),
    (r"\bcontinue\s+music\b", "resume music"),
    (r"\bread\s+head\s*lines?\b", "read headlines"),
    (r"\bread\s+the\s+head\s*lines?\b", "read headlines"),
    (r"\bread\s+the\s+news\b", "read headlines"),
    (r"\blatest\s+head\s*lines?\b", "read headlines"),
    (r"\bnews\s+head\s*lines?\b", "read headlines"),
    (r"\btop\s+stories\b", "read headlines"),
    (r"\bopen\s+what'?s?\s*app\b", "open whatsapp"),
    (r"\bopen\s+whats\s+app\b", "open whatsapp"),
    (r"\bwhat'?s?\s*app\b", "whatsapp"),
    (r"\bset\s+volume\s+two\b", "set volume to"),
    (r"\breduce\s+the\s+volume\b", "reduce volume"),
    (r"\bincrease\s+the\s+volume\b", "increase volume"),
    (r"\bturn\s+up\s+the\s+volume\b", "increase volume"),
    (r"\bturn\s+down\s+the\s+volume\b", "reduce volume"),
    (r"\bmute\s+the\s+music\b", "mute music"),
    (r"\bunmute\s+the\s+music\b", "unmute music"),
    (r"\bend\s+the\s+call\b", "end the call"),
    (r"\bhang\s+up\b", "end the call"),
    (r"\bstop\s+listening\b", "stop listening"),
    (r"\bhey\s+jervis\b", "hey friday"),
    (r"\bjervis\b", "friday"),
    (r"\bharvis\b", "friday"),
    (r"\bhervis\b", "friday"),
    (r"\bjarvus\b", "friday"),
    (r"\bjarviz\b", "friday"),
    (r"\bfridays\b", "friday"),
    (r"\bjarvies\b", "friday"),
    (r"\bjadwish\b", "friday"),
    (r"\bfridayh\b", "friday"),
    (r"\bjarwish\b", "friday"),
    (r"\bhi[,]?\s+i(?:'m| am)\s+friday\b", "hi friday"),
    (r"\bhello[,]?\s+i(?:'m| am)\s+friday\b", "hello friday"),
    (r"\bhey[,]?\s+i(?:'m| am)\s+friday\b", "hey friday"),
    (r"\b(hi|hello|hey)[,]?\s+verde\b", r"\1 friday"),
    (r"\bopen\s+you\s*tube\b", "open youtube"),
    (r"\byou\s+tube\b", "youtube"),
    # Phonetic mishearings for Q&A
    (r"\bwhat is nice in a mind\b", "what is niacinamide"),
    (r"\bwhat is nice inamide\b", "what is niacinamide"),
    (r"\bwhat is niacin a mind\b", "what is niacinamide"),
    (r"\bwhat is niacinamide\b", "what is niacinamide"),
    (r"\bnice in a mind\b", "niacinamide"),
    (r"\bwhat is the time\b", "what is the time"),
    (r"\bplay the music\b", "play music"),
    (r"\bplay some music\b", "play music"),
    (r"\b(this\s+is\s+for|watch\s+for|such\s+for|look\s+for)\s+(.+?)\s+(in|on)\s+whatsapp\b", r"search for \2 in whatsapp"),
    (r"\b(this\s+is\s+for|watch\s+for|such\s+for)\s+([a-zA-Z]+)\b", r"search for \2"),
    (r"\bsathish\b", "satish"),
    (r"\b(send|tell|text|message)\s+([a-zA-Z]+)\s+high\b", r"\1 \2 hi"),
    (r"\b(send|tell|text|message)\s+high\s+to\s+([a-zA-Z]+)\b", r"\1 hi to \2"),
    (r"\bhigh\s+to\s+([a-zA-Z]+)\b", r"hi to \1"),
    (r"\bsearch for laptops\b", "search for laptops"),
    (r"\bsearch laptops\b", "search for laptops"),
    (r"\bgoogle laptops\b", "search for laptops"),
]

# Short commands we can fuzzy-match when STT is close but not exact
_KNOWN_SHORT_COMMANDS = [
    "play music",
    "play some music",
    "stop music",
    "pause music",
    "resume music",
    "restart music",
    "mute music",
    "unmute music",
    "read headlines",
    "open chrome",
    "open whatsapp",
    "open youtube",
    "hi friday",
    "hello friday",
    "hey friday",
    "end the call",
    "stop listening",
    "what can you do",
    "tell me a joke",
    "good morning",
    "good evening",
    "reduce volume",
    "increase volume",
    "mute",
    "unmute",
    "music status",
]


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _deduplicate_repetition(text: str) -> str:
    """Removes Whisper repetition artifacts like 'Clause, clause' or 'Sentence. Sentence.'"""
    if not text:
        return text
    for sep in [", ", ". ", " - "]:
        parts = text.split(sep)
        if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
            return parts[0].strip()
        if len(parts) >= 2 and parts[-1].strip().lower() == parts[-2].strip().lower():
            return sep.join(parts[:-1]).strip()
    return text


def correct_transcript(text: str) -> str:
    """Fix common STT mistakes while preserving the user's intent."""
    if not text:
        return text

    cleaned = _deduplicate_repetition(_collapse_ws(text))
    original = cleaned

    for pattern, replacement in _PHRASE_FIXES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = _deduplicate_repetition(_collapse_ws(cleaned)).rstrip(".!?,")
    words = cleaned.split()
    if 1 <= len(words) <= 6:
        match = get_close_matches(cleaned.lower(), _KNOWN_SHORT_COMMANDS, n=1, cutoff=0.82)
        if match:
            cleaned = match[0]

    if cleaned != original:
        print(f"[STT Correct] {original!r} -> {cleaned!r}")

    return cleaned