"""Pick a real microphone. Avoid stereo-mix / loopback that hears Friday, not you."""

from __future__ import annotations

import sounddevice as sd

_AVOID = (
    "stereo mix",
    "what u hear",
    "wave out",
    "loopback",
    "cable output",
    "cable input",
    "voicemeeter out",
    "speakers",
    "primary sound capture",
)
_PREFER = (
    "macbook",
    "built-in",
    "microphone",
    "headset",
    "airpods",
    "headphone",
    "array",
    "usb audio",
    "mic",
)

_cached: int | None = None


def _score(name: str, is_default: bool) -> int:
    low = (name or "").lower()
    if any(tok in low for tok in _AVOID):
        return -100
    score = 10 if is_default else 0
    for i, tok in enumerate(_PREFER):
        if tok in low:
            score += 40 - i
            break
    return score


def pick_input_device() -> int | None:
    """Return a sounddevice input index, or None for the system default."""
    global _cached
    if _cached is not None:
        return _cached

    try:
        devices = sd.query_devices()
        default_idx = sd.default.device[0] if sd.default.device is not None else None
    except Exception as exc:
        print(f"[STT] Could not list audio devices: {exc}")
        return None

    candidates = []
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels") or 0) < 1:
            continue
        name = str(dev.get("name") or "")
        score = _score(name, idx == default_idx)
        if score < 0:
            print(f"[STT] Skipping capture device: {name}")
            continue
        candidates.append((score, idx, name))

    if not candidates:
        print("[STT] No preferred microphone found — using system default")
        return None

    candidates.sort(key=lambda row: row[0], reverse=True)
    score, idx, name = candidates[0]
    _cached = idx
    print(f"[STT] Using microphone [{idx}] {name} (score={score})")
    return idx
