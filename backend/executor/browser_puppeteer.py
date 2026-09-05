"""
browser_puppeteer.py — High-level FRIDAY tools backed by Puppeteer.
==================================================================
Intents / helpers:
  - browser_action (generic)
  - youtube / youtube music play
  - spotify login + search
  - scroll speed test
  - click / type / scroll primitives
"""

from __future__ import annotations

from typing import Any

from executor.puppeteer_client import command, tool_result


def browser_action(params: dict | None = None) -> tuple[bool, str]:
    """
    Generic entry: params.action + extra fields.
    Example: {"action": "youtube_play", "query": "AC/DC Back in Black"}
    """
    params = params or {}
    action = params.get("action") or params.get("op") or "status"
    extra = {k: v for k, v in params.items() if k not in ("action", "op")}
    result = command(action, **extra)
    return tool_result(result)


def youtube_play_puppeteer(song: str, service: str = "youtube") -> tuple[bool, str]:
    action = "youtube_music_play" if "music" in (service or "").lower() else "youtube_play"
    result = command(
        action,
        timeout=240.0,
        query=song or "AC/DC Back in Black",
        song=song,
    )
    label = "YouTube Music" if "music" in action else "YouTube"
    return tool_result(result, default_ok_message=f"Playing {song} on {label}.")


def spotify_login_puppeteer(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "spotify_login",
        timeout=120.0,
        email=params.get("email", ""),
        password=params.get("password", ""),
        preferGoogle=params.get("preferGoogle", params.get("prefer_google", True)),
        method=params.get("method") or "google",
    )
    # Soft success: Google click / login page opened
    if result.get("needsCredentials") or result.get("method") == "google":
        return True, result.get("message", "Spotify login flow opened.")
    return tool_result(result, default_ok_message="Spotify login flow completed.")


def spotify_search_puppeteer(query: str, play: bool = True) -> tuple[bool, str]:
    result = command("spotify_search", timeout=120.0, query=query, play=play)
    return tool_result(result, default_ok_message=f"Spotify search for {query} opened.")


def scroll_test_puppeteer(params: dict | None = None) -> tuple[bool, str]:
    """Demo-friendly smooth scroll: exactly ~5s gap between steps by default."""
    params = params or {}
    mode = (params.get("mode") or "demo").lower()
    is_bench = mode in ("benchmark", "fast")
    result = command(
        "scroll_test",
        timeout=180.0,
        url=params.get("url") or "https://en.wikipedia.org/wiki/AC/DC",
        pixels=int(params.get("pixels") or (900 if is_bench else 350)),
        times=int(params.get("times") or (8 if is_bench else 4)),
        # Hard 5000ms gap for demos (user-requested)
        delayMs=int(params.get("delayMs") or params.get("delay_ms") or (100 if is_bench else 5000)),
        behavior=params.get("behavior") or ("instant" if is_bench else "smooth"),
        settleMs=int(params.get("settleMs") or params.get("settle_ms") or (0 if is_bench else 800)),
        mode=mode,
    )
    return tool_result(result, default_ok_message="Scroll test complete.")


def web_search_puppeteer(params: dict | None = None) -> tuple[bool, str]:
    """Open search results beside Friday. No scroll demo."""
    params = params or {}
    query = (params.get("query") or params.get("q") or params.get("text") or "").strip()
    if not query:
        return False, "What should I search for?"
    from executor.search_briefing import run_search_briefing
    briefing = run_search_briefing(query)
    if briefing.get("ok"):
        n = len(briefing.get("results") or [])
        return True, f"Opened search results for {query}." + (f" Found {n} sources." if n else "")
    return False, briefing.get("error") or f"Could not open search for {query}."


def browser_click(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    if params.get("text"):
        result = command("click_text", text=params["text"])
    else:
        result = command("click", selector=params.get("selector", "body"))
    return tool_result(result, default_ok_message="Clicked.")


def browser_type(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "type",
        selector=params.get("selector", "input"),
        text=params.get("text", ""),
    )
    return tool_result(result, default_ok_message="Typed.")


def browser_scroll(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    result = command(
        "scroll",
        pixels=int(params.get("pixels") or 380),
        direction=params.get("direction") or "down",
        times=int(params.get("times") or 3),
        delayMs=int(params.get("delayMs") or 5000),
        behavior=params.get("behavior") or "smooth",
        settleMs=int(params.get("settleMs") or 900),
    )
    return tool_result(result, default_ok_message="Scrolled.")


def browser_navigate(params: dict | None = None) -> tuple[bool, str]:
    params = params or {}
    url = params.get("url") or params.get("href") or "about:blank"
    result = command("navigate", url=url)
    return tool_result(result, default_ok_message=f"Opened {url}.")


# Registry-friendly lambdas receive params dict
def play_youtube_search_pp(params: dict) -> tuple[bool, str]:
    # Prefer YouTube Music for better playback UX
    return youtube_play_puppeteer(
        params.get("song") or params.get("query") or "", service="youtube_music"
    )


def play_youtube_music_pp(params: dict) -> tuple[bool, str]:
    return youtube_play_puppeteer(
        params.get("song") or params.get("query") or "", service="youtube_music"
    )


def play_spotify_pp(params: dict) -> tuple[bool, str]:
    song = params.get("song") or params.get("query") or ""
    # Ensure session if possible, then search+play
    login_res = command("spotify_login", timeout=90.0)
    if login_res.get("needsCredentials") and not song:
        return True, login_res.get("message", "Spotify login page opened.")
    if song:
        return spotify_search_puppeteer(song, play=True)
    return tool_result(login_res, default_ok_message="Spotify ready.")


def linkedin_browser_demo(params: dict | None = None) -> tuple[bool, str]:
    """
    One-shot demo sequence (no Spotify):
      1) Slow scroll (5 second gap between steps)
      2) Play song on YouTube Music
    """
    params = params or {}
    song = (params.get("song") or params.get("query") or "AC/DC Back in Black").strip()
    scroll_url = params.get("url") or params.get("scroll_url") or "https://en.wikipedia.org/wiki/AC/DC"
    times = int(params.get("times") or params.get("scroll_times") or 4)
    pixels = int(params.get("pixels") or params.get("scroll_pixels") or 350)
    # Always YouTube Music unless explicitly overridden
    service = (params.get("service") or "youtube_music").lower()
    if service in ("youtube", "yt"):
        service = "youtube_music"

    steps: list[str] = []
    all_ok = True

    # 1) Scroll — hard 5s gap between smooth steps
    try:
        ok, msg = scroll_test_puppeteer(
            {
                "url": scroll_url,
                "times": times,
                "pixels": pixels,
                "mode": "demo",
                "delayMs": 5000,
                "behavior": "smooth",
                "settleMs": 800,
            }
        )
        steps.append(f"1) Scroll: {msg}")
        all_ok = all_ok and ok
    except Exception as exc:
        all_ok = False
        steps.append(f"1) Scroll failed: {exc}")

    # 2) YouTube Music
    try:
        ok, msg = youtube_play_puppeteer(song, service="youtube_music")
        steps.append(f"2) YouTube Music: {msg}")
        all_ok = all_ok and ok
    except Exception as exc:
        all_ok = False
        steps.append(f"2) YouTube Music failed: {exc}")

    summary = "Demo complete. " + " | ".join(steps)
    return all_ok, summary
