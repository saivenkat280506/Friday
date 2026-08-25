"""
local_music_player.py — In-process local audio playback for companion controls.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from executor.music_player import _find_local_track, _iter_local_audio_files, _pick_latest_track

_lock = threading.Lock()
_playlist: list[Path] = []
_current_index: int = -1
_current_path: Path | None = None
_is_playing = False
_volume = 0.65


def _ensure_mixer() -> bool:
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.set_volume(_volume)
        return True
    except Exception as exc:
        print(f"[LocalMusic] pygame init failed: {exc}")
        return False


def _build_playlist(anchor: Path | None = None) -> list[Path]:
    tracks = _iter_local_audio_files()
    if not tracks:
        return []
    tracks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if anchor and anchor in tracks:
        idx = tracks.index(anchor)
        return tracks[idx:] + tracks[:idx]
    return tracks


def _play_path(path: Path) -> tuple[bool, str]:
    global _current_path, _is_playing, _current_index

    if not _ensure_mixer():
        return False, "Local player unavailable."

    import pygame

    try:
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.set_volume(_volume)
        pygame.mixer.music.play()
        _current_path = path
        _is_playing = True
        if path in _playlist:
            _current_index = _playlist.index(path)
        return True, path.stem
    except Exception as exc:
        _is_playing = False
        return False, f"Could not play {path.name}: {exc}"


def play_track(query: str = "") -> tuple[bool, str, str]:
    """Play a local track. Returns (ok, display_name, path_str)."""
    global _playlist, _current_index

    with _lock:
        track = _find_local_track(query)
        if not track:
            label = f" matching {query!r}" if query else ""
            return False, f"No local audio file{label}", ""

        _playlist = _build_playlist(track)
        _current_index = 0
        ok, name = _play_path(track)
        if ok:
            return True, name, str(track)
        return False, name, ""


def toggle_playback() -> tuple[bool, str, bool]:
    """Pause or resume current local track. Returns (ok, song, is_playing)."""
    global _is_playing
    with _lock:
        if not _ensure_mixer() or _current_path is None:
            return False, "", False

        import pygame

        if _is_playing:
            pygame.mixer.music.pause()
            _is_playing = False
        else:
            pygame.mixer.music.unpause()
            _is_playing = True
        return True, _current_path.stem, _is_playing


def pause() -> tuple[bool, str]:
    global _is_playing
    with _lock:
        if not _ensure_mixer():
            return False, ""
        import pygame

        if _current_path and _is_playing:
            pygame.mixer.music.pause()
            _is_playing = False
            return True, _current_path.stem
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            _is_playing = False
        return _current_path is not None, (_current_path.stem if _current_path else "")


def resume() -> tuple[bool, str]:
    global _is_playing
    with _lock:
        if not _ensure_mixer() or _current_path is None:
            return False, ""
        import pygame

        if not _is_playing:
            pygame.mixer.music.unpause()
            _is_playing = True
        return True, _current_path.stem


def stop() -> None:
    global _is_playing, _current_path, _current_index

    with _lock:
        if _ensure_mixer():
            import pygame

            pygame.mixer.music.stop()
        _is_playing = False
        _current_path = None
        _current_index = -1


def next_track() -> tuple[bool, str]:
    global _current_index

    with _lock:
        if not _playlist:
            latest = _pick_latest_track(_iter_local_audio_files())
            if not latest:
                return False, ""
            _playlist[:] = _build_playlist(latest)
            _current_index = 0
            ok, name = _play_path(_playlist[0])
            return ok, name

        _current_index = (_current_index + 1) % len(_playlist)
        ok, name = _play_path(_playlist[_current_index])
        return ok, name


def previous_track() -> tuple[bool, str]:
    global _current_index

    with _lock:
        if not _playlist:
            return False, ""
        _current_index = (_current_index - 1) % len(_playlist)
        ok, name = _play_path(_playlist[_current_index])
        return ok, name


def set_volume(level: float) -> float:
    """Set local music volume 0.0–1.0. Returns applied level."""
    global _volume
    with _lock:
        _volume = max(0.0, min(1.0, float(level)))
        if _ensure_mixer():
            import pygame

            pygame.mixer.music.set_volume(_volume)
        return _volume


def adjust_volume(delta: float) -> float:
    with _lock:
        return set_volume(_volume + float(delta))


def get_volume() -> float:
    with _lock:
        return _volume


def get_playback_state() -> dict:
    with _lock:
        return {
            "song": _current_path.stem if _current_path else "",
            "path": str(_current_path) if _current_path else "",
            "is_playing": _is_playing,
            "has_track": _current_path is not None,
            "volume": round(_volume, 2),
        }


def sync_playing_flag() -> bool:
    """Refresh is_playing from pygame when the track ends naturally."""
    global _is_playing

    with _lock:
        if not _ensure_mixer():
            return False
        import pygame

        busy = pygame.mixer.music.get_busy()
        if _current_path and not busy:
            _is_playing = False
        return _is_playing