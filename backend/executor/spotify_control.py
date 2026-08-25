"""
spotify_control.py — Control Spotify Desktop on Windows via URI + media keys.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.parse


def is_spotify_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "Spotify.exe" in out
    except Exception:
        return False


def _focus_spotify_window() -> bool:
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                title = (window.window_text() or "").lower()
            except Exception:
                continue
            if "spotify" in title:
                try:
                    window.set_focus()
                    return True
                except Exception:
                    pass
    except Exception as exc:
        print(f"[Spotify] Focus failed: {exc}")
    return False


def _media_key(action: str) -> None:
    try:
        import pyautogui

        key_map = {
            "play": "playpause",
            "pause": "playpause",
            "next": "nexttrack",
            "prev": "prevtrack",
        }
        pyautogui.press(key_map.get(action, "playpause"))
    except Exception as exc:
        print(f"[Spotify] Media key failed: {exc}")


def play_pause() -> tuple[bool, str]:
    if not is_spotify_running():
        return False, "Spotify desktop is not running."
    _focus_spotify_window()
    time.sleep(0.2)
    _media_key("play")
    return True, "Toggled Spotify playback."


def next_track() -> tuple[bool, str]:
    if not is_spotify_running():
        return False, "Spotify desktop is not running."
    _focus_spotify_window()
    _media_key("next")
    return True, "Skipped to next track on Spotify."


def previous_track() -> tuple[bool, str]:
    if not is_spotify_running():
        return False, "Spotify desktop is not running."
    _focus_spotify_window()
    _media_key("prev")
    return True, "Previous track on Spotify."


def play_song(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return play_pause()

    encoded = urllib.parse.quote(song.strip())
    uri = f"spotify:search:{encoded}"
    try:
        os.startfile(uri)
    except OSError:
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", uri],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            return False, f"Could not open Spotify: {exc}"

    time.sleep(2.5)
    _focus_spotify_window()
    time.sleep(0.3)
    _media_key("play")
    return True, f"Playing {song} on Spotify desktop."