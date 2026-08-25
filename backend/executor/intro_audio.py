"""
intro_audio.py — FRIDAY spoken introduction clip
Plays the bundled voice preview when the user asks FRIDAY to introduce itself.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_INTRO_FILENAMES = (
    "friday-intro.mp3",
    "voice_preview_friday (1).mp3",
    "voice_preview_friday.mp3",
    "FRIDAY voice.mp3",
)


def resolve_friday_intro_track() -> Path | None:
    """Resolve the FRIDAY introduction clip from bundled assets or Downloads."""
    bundled = Path(__file__).resolve().parent.parent / "assets" / "audio"
    search_roots = (
        bundled,
        Path.home() / "Downloads",
        Path.home() / "Downloads" / "Audio",
    )
    for root in search_roots:
        if not root.exists():
            continue
        for name in _INTRO_FILENAMES:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def stop_friday_intro() -> None:
    """Stop bundled intro playback if still running."""
    try:
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
    except Exception:
        pass


def play_friday_intro() -> tuple[bool, str]:
    """Play the FRIDAY introduction audio and block until playback finishes."""
    track = resolve_friday_intro_track()
    if not track:
        return False, "I couldn't find my introduction audio, Boss."

    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(str(track))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        pygame.mixer.music.unload()
        return True, "Playing my introduction, Boss."
    except Exception as exc:
        print(f"[IntroAudio] pygame playback failed ({exc}), falling back to system player")
        try:
            os.startfile(str(track))
            return True, "Playing my introduction, Boss."
        except OSError as start_exc:
            return False, f"I found my introduction clip, but could not play it: {start_exc}"