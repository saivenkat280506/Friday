"""
music_player.py — Platform-aware music playback for FRIDAY
Parses play commands, detects Spotify / YouTube / YouTube Music, resolves
direct track/video URLs, and triggers browser playback (not just search).
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
import os

DEFAULT_PLATFORM = "spotify"
DEFAULT_BARE_PLATFORM = "local"
LOCAL_AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac"}
FRIDAY_BUNDLE_MUSIC = Path(__file__).resolve().parent.parent / "local_music"
DEFAULT_GARAGE_TRACK = FRIDAY_BUNDLE_MUSIC / "garage_music.mp3"
LOCAL_MUSIC_ROOTS = (
    FRIDAY_BUNDLE_MUSIC,
    Path.home() / "Music",
    Path.home() / "Downloads" / "Audio",
    Path.home() / "Downloads",
)
LOCAL_MUSIC_PREFERRED_ROOTS = (
    Path.home() / "Music",
    Path.home() / "Downloads" / "Audio",
)
_LOCAL_TRACK_SKIP_TOKENS = frozenset({
    "voice", "voices", "lines", "tts", "speech", "trailer", "jarvis", "friday", "preview", "mcu",
})

# Curated fallbacks — avoids flaky search + fixes AC/DC slash encoding issues
KNOWN_SPOTIFY_TRACKS: dict[str, str] = {
    "ac/dc back in black": "2iEGj7kAwH7HAa5epwYwLB",
    "back in black": "2iEGj7kAwH7HAa5epwYwLB",
    "back in black ac/dc": "2iEGj7kAwH7HAa5epwYwLB",
}

_PLATFORM_ALIASES = {
    "spotify": "spotify",
    "youtube music": "youtube_music",
    "yt music": "youtube_music",
    "youtube": "youtube",
}


def parse_music_command(text: str) -> dict:
    """
    Parse a natural-language play request.
    Returns {"song": str, "platform": str} or {} for bare play/resume.
    """
    lower = text.lower().strip().rstrip("?!., ")

    for filler in ("hey friday", "ok friday", "okay friday", "friday,"):
        if lower.startswith(filler):
            lower = lower[len(filler):].strip()

    if re.fullmatch(r"(play|resume)(\s+(it|again|that))?", lower):
        return {}

    platform = DEFAULT_PLATFORM
    local_match = re.search(
        r"\b(?:from|on|in)\s+(?:my\s+)?(?:local|computer|pc)\b|\blocal\s+music\b",
        lower,
    )
    platform_match = re.search(
        r"\b(on|in)\s+(spotify|youtube\s+music|yt\s+music|youtube)\b",
        lower,
    )
    if local_match or re.search(r"\bgarage\b", lower):
        platform = "local"
    elif platform_match:
        platform = _PLATFORM_ALIASES.get(platform_match.group(2).strip(), DEFAULT_BARE_PLATFORM)

    song = lower
    song = re.sub(r"^(play|start|put on|queue)\s+", "", song)
    song = re.sub(
        r"\b(on|in)\s+(spotify|youtube\s+music|yt\s+music|youtube)\b.*$",
        "",
        song,
    )
    song = re.sub(r"\b(?:from|on|in)\s+(?:my\s+)?(?:local|computer|pc)\b", "", song)
    song = re.sub(r"\blocal\s+music\b", "", song)
    song = re.sub(r"\b(for me|please|now|boss)\b", "", song)
    song = re.sub(r"\b(some\s+)?music\b", "", song)
    song = re.sub(r"\b(a\s+)?song\b", "", song)
    song = re.sub(r"\bthe\s+(song|track)\b", "", song)
    song = song.strip(" .,!?")

    if not song:
        platform = DEFAULT_BARE_PLATFORM
        song = ""
    elif platform == "local" and re.fullmatch(r"garage", song):
        song = ""

    return {"song": song, "platform": platform}


def _is_skipped_local_track(stem: str) -> bool:
    """Skip assistant voice clips and other non-music samples."""
    normalized = re.sub(r"[\s_\-()]+", " ", stem.lower()).strip()
    tokens = set(normalized.split())
    if tokens & _LOCAL_TRACK_SKIP_TOKENS:
        return True
    return "not 100" in normalized


def _fetch_youtube_video_id(song: str) -> str | None:
    """Resolve the first YouTube video id for a song query."""
    if not song or not song.strip():
        return None
    query = urllib.parse.quote_plus(song)
    url = f"https://www.youtube.com/results?search_query={query}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        ids = re.findall(r'"videoId":"([^"]{11})"', html)
        return ids[0] if ids else None
    except Exception as exc:
        print(f"[MusicPlayer] YouTube lookup failed: {exc}")
        return None


def _normalize_song_key(song: str) -> str:
    return re.sub(r"\s+", " ", song.lower().strip())


def _iter_local_audio_files(roots: tuple[Path, ...] = LOCAL_MUSIC_ROOTS) -> list[Path]:
    """Collect playable local audio files from the user's music folders."""
    candidates: list[Path] = []
    seen: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        try:
            for candidate in root.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in LOCAL_AUDIO_SUFFIXES:
                    continue
                key = str(candidate.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                if _is_skipped_local_track(candidate.stem):
                    continue
                candidates.append(candidate)
        except OSError:
            continue

    return candidates


def _pick_latest_track(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _find_local_track(query: str) -> Path | None:
    """Find a local audio file. Empty query prefers bundled garage music."""
    needle = _normalize_song_key(query).replace(" ", "")

    if not needle:
        if DEFAULT_GARAGE_TRACK.is_file():
            return DEFAULT_GARAGE_TRACK
        garage_hits = [
            p
            for p in _iter_local_audio_files()
            if "garage" in _normalize_song_key(p.stem)
        ]
        pick = _pick_latest_track(garage_hits)
        if pick is not None:
            return pick

    if needle:
        candidates = _iter_local_audio_files()
        matches = [
            candidate
            for candidate in candidates
            if needle in _normalize_song_key(candidate.stem).replace(" ", "")
        ]
        return _pick_latest_track(matches)

    preferred = _pick_latest_track(_iter_local_audio_files(LOCAL_MUSIC_PREFERRED_ROOTS))
    if preferred is not None:
        return preferred
    return _pick_latest_track(_iter_local_audio_files((Path.home() / "Downloads",)))


def play_local_music(song: str) -> tuple[bool, str]:
    from executor.local_music_player import play_track

    ok, name, _path = play_track(song)
    if not ok:
        return False, f"{name}, Boss." if name else "I couldn't find a local audio file, Boss."
    return True, f"Playing local track: {name}, Boss."


def _spotify_encode(song: str) -> str:
    """Encode song names for Spotify URLs — slashes in AC/DC must become %2F."""
    return urllib.parse.quote(song.strip(), safe="")


def _is_valid_spotify_track_id(track_id: str) -> bool:
    if not track_id or not re.fullmatch(r"[a-zA-Z0-9]{22}", track_id):
        return False
    url = f"https://open.spotify.com/track/{track_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        import httpx
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return False
            lower = resp.text.lower()
            return "page not found" not in lower and "something went wrong" not in lower
    except Exception as exc:
        print(f"[MusicPlayer] Spotify track validation failed for {track_id}: {exc}")
        return False


def _resolve_spotify_track_id(song: str) -> str | None:
    """Resolve a Spotify track id via known catalog, then public web search."""
    if not song or not song.strip():
        return None

    key = _normalize_song_key(song)
    known = KNOWN_SPOTIFY_TRACKS.get(key)
    if known and _is_valid_spotify_track_id(known):
        return known

    queries = [
        f"{song} spotify track",
        f"site:open.spotify.com/track {song}",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        import httpx
        with httpx.Client(headers=headers, timeout=12.0, follow_redirects=True) as client:
            for query in queries:
                search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
                try:
                    resp = client.get(search_url)
                    if resp.status_code not in (200, 202):
                        continue
                    ids = re.findall(r"open\.spotify\.com/track/([a-zA-Z0-9]{22})", resp.text)
                    for track_id in ids:
                        if _is_valid_spotify_track_id(track_id):
                            return track_id
                except Exception as exc:
                    print(f"[MusicPlayer] Spotify lookup query failed ({query!r}): {exc}")
    except Exception as exc:
        print(f"[MusicPlayer] Spotify lookup failed: {exc}")

    return known if known else None


def _open_url(url: str) -> bool:
    from executor.automation import open_url_in_chrome
    return bool(open_url_in_chrome(url))


def _focus_browser_window(*title_keywords: str) -> bool:
    """Bring the browser window to the foreground."""
    keywords = [k.lower() for k in title_keywords if k]
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                title = (window.window_text() or "").lower()
            except Exception:
                continue
            if any(k in title for k in keywords):
                try:
                    window.set_focus()
                    return True
                except Exception:
                    pass
    except Exception as exc:
        print(f"[MusicPlayer] Browser focus failed: {exc}")
    return False


def _trigger_web_playback(platform: str, wait_seconds: float = 5.0) -> None:
    """
    Browsers block autoplay with sound — focus the tab and send a play shortcut.
    """
    time.sleep(wait_seconds)

    title_hints = {
        "spotify": ("spotify", "chrome", "edge", "firefox", "brave"),
        "youtube": ("youtube", "chrome", "edge", "firefox", "brave"),
        "youtube_music": ("youtube music", "youtube", "chrome", "edge", "firefox", "brave"),
    }
    _focus_browser_window(*title_hints.get(platform, ("chrome", "edge", "firefox", "brave")))

    try:
        import pyautogui
        time.sleep(0.4)
        if platform in ("youtube", "youtube_music"):
            pyautogui.press("k")
        else:
            pyautogui.press("space")
        time.sleep(0.2)
        pyautogui.press("space")
    except Exception as exc:
        print(f"[MusicPlayer] Playback key assist failed: {exc}")


def _try_browser_recipe(recipe: str, params: dict[str, str], task: str) -> tuple[bool, str] | None:
    try:
        import asyncio
        from executor.browser_agent import run_browser_recipe

        ok, msg = asyncio.run(run_browser_recipe(recipe, params, task=task, mode="headed"))
        if ok:
            return True, msg
    except Exception as exc:
        print(f"[MusicPlayer] Browser recipe {recipe} failed: {exc}")
    return None


def play_on_youtube(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "No song specified."
    browser = _try_browser_recipe("playYouTube", {"song": song}, f"play {song} on youtube")
    if browser:
        return browser
    vid = _fetch_youtube_video_id(song)
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}&autoplay=1"
        _open_url(url)
        _trigger_web_playback("youtube")
        return True, f"Playing {song} on YouTube."
    query = urllib.parse.quote_plus(song)
    _open_url(f"https://www.youtube.com/results?search_query={query}")
    return True, f"Opened YouTube search for {song}."


def play_on_youtube_music(song: str) -> tuple[bool, str]:
    if not song or not song.strip():
        return False, "No song specified."
    browser = _try_browser_recipe("playYouTubeMusic", {"song": song}, f"play {song} on youtube music")
    if browser:
        return browser
    vid = _fetch_youtube_video_id(song)
    if vid:
        url = f"https://music.youtube.com/watch?v={vid}&autoplay=1"
        _open_url(url)
        _trigger_web_playback("youtube_music")
        return True, f"Playing {song} on YouTube Music."
    query = urllib.parse.quote_plus(song)
    _open_url(f"https://music.youtube.com/search?q={query}")
    return True, f"Opened YouTube Music search for {song}."


def play_on_spotify(song: str) -> tuple[bool, str]:
    """Play on Spotify Desktop when available, otherwise Spotify Web."""
    if not song or not song.strip():
        return False, "No song specified."

    from executor.spotify_control import is_spotify_running, play_song as spotify_desktop_play

    if is_spotify_running():
        ok, msg = spotify_desktop_play(song)
        if ok:
            return True, msg

    browser = _try_browser_recipe("playSpotify", {"song": song}, f"play {song} on spotify")
    if browser:
        return browser

    track_id = _resolve_spotify_track_id(song)
    if track_id:
        url = f"https://open.spotify.com/track/{track_id}"
        _open_url(url)
        _trigger_web_playback("spotify")
        return True, f"Playing {song} on Spotify."

    encoded = _spotify_encode(song)
    _open_url(f"https://open.spotify.com/search/{encoded}")
    _trigger_web_playback("spotify", wait_seconds=6.0)
    return True, f"Opened Spotify web search for {song}."


def play_music(song: str, platform: str = DEFAULT_PLATFORM) -> tuple[bool, str]:
    platform = (platform or DEFAULT_PLATFORM).lower().replace(" ", "_")
    if platform == "local":
        return play_local_music(song)
    if platform == "spotify":
        return play_on_spotify(song)
    if platform == "youtube":
        return play_on_youtube(song)
    return play_on_youtube_music(song)
