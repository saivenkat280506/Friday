"""
music_services.py — Music Playback & Music Control for FRIDAY
=============================================================
Playback services: local garage, YouTube Search, YouTube Music, Spotify.
Music control: play / stop / pause / resume / volume / mute / status.
"""

import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import pygame

LOCAL_MUSIC_DIR = Path(__file__).resolve().parent.parent / "local_music"
GARAGE_TRACK = LOCAL_MUSIC_DIR / "garage_music.mp3"

_mixer_lock = threading.Lock()
_local_paused = False
_music_volume = 50          # 0–100 target for local garage track only (not system volume)
_music_muted = False
_music_muted_level = 50     # restore level after unmute
_current_track = ""
_duck_active = False        # True while volume is temporarily lowered (speech/listen)
_duck_level = 20            # temporary level while ducked
# Listening: duck to 20%. Speaking over music: duck slightly so Friday is clear.
DUCK_LISTEN_PCT = 20
DUCK_SPEAK_PCT = 25
DEFAULT_GARAGE_VOLUME = 50


def _ensure_mixer():
    if not pygame.mixer.get_init():
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.mixer.init()


def _apply_music_volume():
    """Apply effective volume to pygame mixer only (never touches macOS master volume)."""
    if not pygame.mixer.get_init():
        return
    if _music_muted:
        level = 0.0
    elif _duck_active:
        level = max(0.0, min(1.0, _duck_level / 100.0))
    else:
        level = max(0.0, min(1.0, _music_volume / 100.0))
    pygame.mixer.music.set_volume(level)


def get_music_volume() -> int:
    return _music_volume


def set_music_volume(level: int) -> tuple[bool, str]:
    global _music_volume, _music_muted, _duck_active
    _music_volume = max(0, min(100, int(level)))
    _music_muted = False
    _duck_active = False
    with _mixer_lock:
        _apply_music_volume()
    return True, f"Garage music volume set to {_music_volume}, Boss."


def adjust_music_volume(delta: int) -> tuple[bool, str]:
    global _music_volume, _music_muted
    _music_muted = False
    _music_volume = max(0, min(100, _music_volume + int(delta)))
    with _mixer_lock:
        _apply_music_volume()
    direction = "increased" if delta > 0 else "decreased"
    return True, f"Music volume {direction} to {_music_volume}, Boss."


def mute_music() -> tuple[bool, str]:
    global _music_muted, _music_muted_level
    _music_muted_level = _music_volume
    _music_muted = True
    with _mixer_lock:
        _apply_music_volume()
    return True, "Music muted, Boss."


def unmute_music() -> tuple[bool, str]:
    global _music_muted, _music_volume
    _music_muted = False
    _music_volume = _music_muted_level
    with _mixer_lock:
        _apply_music_volume()
    return True, f"Music unmuted at {_music_volume}, Boss."


def stop_local_music() -> bool:
    """Stop background local music playback."""
    global _local_paused, _current_track, _duck_active
    stopped = False
    with _mixer_lock:
        _local_paused = False
        _duck_active = False
        _current_track = ""
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
            stopped = True
    try:
        from executor.volume_control import end_garage_volume_session
        end_garage_volume_session(restore=True)
    except Exception:
        pass
    return stopped


def pause_local_music() -> tuple[bool, str]:
    """Hard pause (user said pause) — keeps track position."""
    global _local_paused, _duck_active
    with _mixer_lock:
        if not pygame.mixer.get_init() or _local_paused:
            if _local_paused:
                return True, "Music is already paused, Boss."
            return False, "No music is playing, Boss."
        if not pygame.mixer.music.get_busy():
            return False, "No music is playing, Boss."
        pygame.mixer.music.pause()
        _local_paused = True
        _duck_active = False
    return True, "Music paused, Boss."


def _duck_to(level: int) -> bool:
    """
    Lower garage track volume without stopping playback.
    Used while FRIDAY speaks or while listening to the user.
    """
    global _duck_active, _duck_level
    with _mixer_lock:
        if not pygame.mixer.get_init():
            return False
        if _local_paused or not pygame.mixer.music.get_busy():
            return False
        _duck_level = max(0, min(100, int(level)))
        _duck_active = True
        _apply_music_volume()
        return True


def _unduck() -> None:
    """Restore garage track to its normal volume (e.g. 50%)."""
    global _duck_active
    with _mixer_lock:
        if not _duck_active:
            return
        _duck_active = False
        if pygame.mixer.get_init() and not _local_paused:
            _apply_music_volume()


def duck_for_speech() -> bool:
    """Lower pygame music only. Never touch the Mac output slider — that clicks."""
    return _duck_to(DUCK_SPEAK_PCT)


def resume_after_speech():
    """Restore garage-track mixer level after FRIDAY finishes speaking."""
    _unduck()


def pause_for_listening() -> bool:
    """
    Do NOT stop music. Duck the garage-track mixer so the mic hears
    the user, not the track.
    """
    try:
        from executor.volume_control import garage_volume_for_listen, is_garage_volume_session
        if is_garage_volume_session() or is_local_music_playing():
            garage_volume_for_listen()
            return True
    except Exception as exc:
        print(f"[Music] pause_for_listening mixer duck failed: {exc}")
    return _duck_to(DUCK_LISTEN_PCT)


def resume_after_listening():
    """Restore mixer play level after STT finishes listening."""
    try:
        from executor.volume_control import garage_volume_for_play, is_garage_volume_session
        if is_garage_volume_session():
            garage_volume_for_play()
            return
    except Exception:
        pass
    _unduck()


def resume_local_music() -> tuple[bool, str]:
    global _local_paused, _duck_active
    with _mixer_lock:
        if not pygame.mixer.get_init():
            return False, "No music to resume, Boss."
        if not _local_paused:
            if pygame.mixer.music.get_busy():
                _duck_active = False
                _apply_music_volume()
                return True, "Music is already playing, Boss."
            return False, "No paused music found, Boss."
        pygame.mixer.music.unpause()
        _local_paused = False
        _duck_active = False
        _apply_music_volume()
    return True, "Music resumed, Boss."


def is_local_music_playing() -> bool:
    if not pygame.mixer.get_init():
        return False
    return pygame.mixer.music.get_busy() and not _local_paused


def get_music_status() -> tuple[bool, str]:
    if _local_paused:
        state = "paused"
    elif is_local_music_playing():
        state = "playing"
    elif pygame.mixer.get_init() and pygame.mixer.music.get_busy():
        state = "paused"
    else:
        state = "stopped"

    track = _current_track or "garage music" if state != "stopped" else "none"
    vol_label = "muted" if _music_muted else str(_music_volume)
    return True, f"Music is {state}. Track: {track}. Volume: {vol_label}, Boss."


def _start_garage_track() -> bool:
    """Load and start the garage track; returns True only if playback is active."""
    global _local_paused, _current_track

    _ensure_mixer()
    _local_paused = False
    _current_track = GARAGE_TRACK.stem.replace("_", " ")
    pygame.mixer.music.stop()
    pygame.mixer.music.unload()
    pygame.mixer.music.load(str(GARAGE_TRACK))
    _apply_music_volume()
    pygame.mixer.music.play()
    time.sleep(0.15)
    return pygame.mixer.music.get_busy()


def play_local_garage(volume: int | None = DEFAULT_GARAGE_VOLUME) -> tuple[bool, str]:
    """
    Play the default garage track from backend/local_music/garage_music.mp3.
    Only adjusts pygame garage-track volume (default 50%) — never macOS master volume.
    """
    global _duck_active
    if not GARAGE_TRACK.exists():
        return False, "Local garage track not found, Boss."

    try:
        target = DEFAULT_GARAGE_VOLUME if volume is None else int(volume)
        set_music_volume(target)
        with _mixer_lock:
            _duck_active = False
            if _start_garage_track():
                return True, f"Playing garage music at {_music_volume} percent, Boss."

            # Retry once after reinitializing the mixer (SDL can miss the first start).
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            _start_garage_track()
            if pygame.mixer.music.get_busy():
                return True, f"Playing garage music at {_music_volume} percent, Boss."

        return False, "Music playback failed to start, Boss."
    except Exception as exc:
        return False, f"Failed to play local music: {exc}"


def restart_music() -> tuple[bool, str]:
    """Restart the current local track from the beginning."""
    if not GARAGE_TRACK.exists():
        return False, "No local track available, Boss."
    return play_local_garage()


def play_youtube_search(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to search for, Boss."
    try:
        query = urllib.parse.quote_plus(song.strip())
        url = f"https://www.youtube.com/results?search_query={query}"
        webbrowser.open(url)
        return True, f"Opened YouTube search for {song.strip()}, Boss."
    except Exception as exc:
        return False, f"Failed to open YouTube: {exc}"


def play_youtube_music(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to play, Boss."
    try:
        query = urllib.parse.quote_plus(song.strip())
        url = f"https://www.youtube.com/results?search_query={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        vid_ids = re.findall(r'"videoId":"([^"]{11})"', html)
        if vid_ids:
            music_url = f"https://music.youtube.com/watch?v={vid_ids[0]}"
            webbrowser.open(music_url)
            return True, f"Playing {song.strip()} on YouTube Music, Boss."

        music_url = f"https://music.youtube.com/search?q={query}"
        webbrowser.open(music_url)
        return True, f"Opened YouTube Music search for {song.strip()}, Boss."
    except Exception as exc:
        return False, f"Failed to play on YouTube Music: {exc}"


def play_spotify(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "Please tell me which song to search for, Boss."
    try:
        query = urllib.parse.quote(song.strip())
        url = f"https://open.spotify.com/search/{query}"
        webbrowser.open(url)
        return True, f"Searching for {song.strip()} on Spotify, Boss."
    except Exception as exc:
        return False, f"Failed to open Spotify: {exc}"


def music_control(params: dict) -> tuple[bool, str]:
    """
    Unified music control handler.
    Actions: stop | pause | resume | restart | status |
             volume_set | volume_up | volume_down | mute | unmute
    """
    action = (params.get("action") or "").lower()

    if action == "stop":
        if stop_local_music():
            return True, "Music stopped, Boss."
        return False, "No music was playing, Boss."

    if action == "pause":
        return pause_local_music()

    if action == "resume":
        return resume_local_music()

    if action == "restart":
        return restart_music()

    if action == "status":
        return get_music_status()

    if action == "volume_set":
        level = params.get("level")
        if level is None:
            return False, "Please specify a music volume between 0 and 100, Boss."
        return set_music_volume(int(level))

    if action == "volume_up":
        amount = int(params.get("amount", 10))
        return adjust_music_volume(abs(amount))

    if action == "volume_down":
        amount = int(params.get("amount", 10))
        return adjust_music_volume(-abs(amount))

    if action == "mute":
        return mute_music()

    if action == "unmute":
        return unmute_music()

    return False, "Unknown music control action, Boss."