"""
audio_prep.py — Release speakers/players so the microphone can open on Windows.
"""

from __future__ import annotations


def release_mic_blockers() -> None:
    """Stop playback that commonly blocks or steals the default audio device."""
    tts_busy = False
    try:
        from tts.pocket_tts import is_tts_active

        tts_busy = is_tts_active()
    except Exception:
        pass

    try:
        from executor.local_music_player import pause

        pause()
    except Exception:
        pass

    if tts_busy:
        # Never kill active TTS output — Windows MME conflicts with the mic.
        return

    try:
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass

    # Do not call sounddevice.stop() here — it kills active mic InputStreams (wake/STT).
    if not tts_busy:
        try:
            from tts.pocket_tts import stop_speech

            stop_speech()
        except Exception:
            pass