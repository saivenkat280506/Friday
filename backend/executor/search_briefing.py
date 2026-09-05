"""
search_briefing.py — Split-screen search: Friday left, browser right.
Opens results in Chrome and returns snippets for a spoken/app summary.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from executor.puppeteer_client import command

_OLD_FOUND_PREFIX = re.compile(
    r"^(?:here(?:'s| is) what i found(?: about .+?)?[:.]\s*"
    r"|based on my search(?:,?\s+here is what i found)?[:.\s]*)",
    re.IGNORECASE,
)


def clean_search_query(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip()).strip(" \"'")
    q = re.sub(r"^(?:can you (?:just )?|please |just )", "", q, flags=re.I)
    q = re.sub(
        r"^(?:search(?: the web)? for |google |look up |look it up )",
        "",
        q,
        flags=re.I,
    )
    return q.strip(" ?!.")


def short_spoken_body(text: str, max_sentences: int = 2, max_words: int = 48) -> str:
    """Keep only a voice-length briefing. Full page stays in the browser."""
    cleaned = _OLD_FOUND_PREFIX.sub("", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    out: list[str] = []
    words = 0
    for part in parts:
        w = part.split()
        if not w:
            continue
        if out and (len(out) >= max_sentences or words + len(w) > max_words):
            break
        out.append(part)
        words += len(w)
        if len(out) >= max_sentences:
            break
    return " ".join(out)


_AD_HOSTS = (
    "booking.com",
    "expedia.",
    "kayak.",
    "hotels.com",
    "tripadvisor.",
    "trivago.",
    "agoda.",
    "airbnb.",
    "hotelscombined.",
)
_AD_TEXT = re.compile(
    r"\b(?:booking\.com|hotels?,?\s+homes|vacation rental|book now|lowest price)\b",
    re.IGNORECASE,
)


def looks_like_ad_text(text: str) -> bool:
    return bool(_AD_TEXT.search(text or ""))


def is_ad_search_result(item: dict) -> bool:
    url = str((item or {}).get("url") or "").lower()
    title = str((item or {}).get("title") or "")
    snippet = str((item or {}).get("snippet") or "")
    if any(host in url for host in _AD_HOSTS):
        return True
    return looks_like_ad_text(title) or looks_like_ad_text(snippet)


def format_found_about(query: str, body: str) -> str:
    """Spoken line: Here's what I found about X. + two short sentences."""
    q = clean_search_query(query)
    raw = _OLD_FOUND_PREFIX.sub("", (body or "").strip())
    short = short_spoken_body(body)
    opener = f"Here's what I found about {q}." if q else "Here's what I found."
    if not short:
        return f"{opener} The full results are on the screen."
    line = f"{opener} {short}"
    if len(raw.split()) > len(short.split()) + 8:
        line += " The rest is on the screen."
    return line

ELECTRON_LAYOUT = "http://127.0.0.1:3930/layout"


def get_work_area() -> dict[str, int]:
    """Primary monitor work area (excludes the menu bar)."""
    from executor.sys_platform import get_work_area as _platform_work_area
    return _platform_work_area()


def split_geometry(area: dict[str, int] | None = None) -> dict[str, Any]:
    area = area or get_work_area()
    gap = 10
    width = int(area.get("width") or 1920)
    height = int(area.get("height") or 1080)
    x = int(area.get("x") or 0)
    y = int(area.get("y") or 0)
    left_w = max(520, int(width * 0.46))
    right_w = max(480, width - left_w - gap)
    return {
        "workArea": {"x": x, "y": y, "width": width, "height": height},
        "friday": {"x": x, "y": y, "width": left_w, "height": height},
        "browser": {
            "left": x + left_w + gap,
            "top": y,
            "width": right_w,
            "height": height,
        },
    }


def _http_json(url: str, payload: dict | None = None, timeout: float = 2.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _snap_friday_macos(bounds: dict) -> bool:
    from executor.sys_platform import set_app_window_bounds
    return set_app_window_bounds(
        "Electron",
        int(bounds["x"]),
        int(bounds["y"]),
        int(bounds["width"]),
        int(bounds["height"]),
    )


def snap_split_layout() -> dict[str, Any]:
    """Put Friday on the left half. Returns geometry including browser bounds."""
    geo = split_geometry()
    try:
        remote = _http_json(ELECTRON_LAYOUT, {"mode": "split-left"}, timeout=2.0)
        if remote.get("ok") and remote.get("browser"):
            print("[SearchBrief] Electron snapped left")
            return remote
        if remote.get("ok") and remote.get("workArea"):
            geo = split_geometry(remote["workArea"])
    except Exception as exc:
        print(f"[SearchBrief] Electron layout server: {exc}")

    if _snap_friday_macos(geo["friday"]):
        print("[SearchBrief] Friday snapped via AppleScript")
    return {"ok": True, **geo}


def run_search_briefing(query: str) -> dict[str, Any]:
    """
    Open the search in a right-hand Chrome window and return extractable facts.
    Does not scroll or run other browser demos.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "query": "", "results": [], "error": "Empty search query."}

    layout = snap_split_layout()
    bounds = layout.get("browser") or split_geometry()["browser"]

    result = command(
        "web_search",
        timeout=70.0,
        query=q,
        bounds=bounds,
    )
    if not result or result.get("ok") is False:
        return {
            "ok": False,
            "query": q,
            "results": [],
            "url": "",
            "featured": "",
            "error": (result or {}).get("error") or (result or {}).get("message") or "Search failed.",
            "layout": layout,
        }

    results = result.get("results") or []
    clean = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if title and url:
            clean.append({"title": title, "url": url, "snippet": snippet})

    return {
        "ok": True,
        "query": q,
        "url": result.get("url") or "",
        "featured": result.get("featured") or "",
        "results": clean[:6],
        "layout": layout,
    }
