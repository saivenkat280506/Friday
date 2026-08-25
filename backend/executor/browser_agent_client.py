"""
browser_agent_client.py — HTTP client for the Node Puppeteer sidecar.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("friday.browser_agent")

DEFAULT_PORT = int(os.getenv("BROWSER_AGENT_PORT", "9477"))
BASE_URL = os.getenv("BROWSER_AGENT_URL", f"http://127.0.0.1:{DEFAULT_PORT}")
TIMEOUT = float(os.getenv("BROWSER_AGENT_TIMEOUT", "90"))


def _resolve_mode(task: str = "", explicit: str | None = None) -> str:
    if explicit in ("headed", "headless"):
        return explicit
    lower = (task or "").lower()
    complex_task = any(
        k in lower
        for k in (
            "browse",
            "article",
            "research",
            "scroll",
            "multiple",
            "read",
            "chatgpt",
            "news",
        )
    )
    default = os.getenv("BROWSER_AGENT_DEFAULT_MODE", "headed")
    if complex_task:
        # Headed keeps Spotify/ChatGPT login in the persistent profile.
        return "headed"
    return default if default in ("headed", "headless") else "headed"


class BrowserAgentClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.base_url}/health")
            r.raise_for_status()
            return r.json()

    async def start_session(self, mode: str = "headed") -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{self.base_url}/session/start", json={"mode": mode})
            r.raise_for_status()
            return r.json()

    async def stop_session(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base_url}/session/stop")
            r.raise_for_status()
            return r.json()

    async def observe(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{self.base_url}/observe")
            r.raise_for_status()
            return r.json()

    async def screenshot(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{self.base_url}/screenshot")
            r.raise_for_status()
            return r.json()

    async def action(self, payload: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
        body = dict(payload)
        if mode:
            body["mode"] = mode
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{self.base_url}/action", json=body)
            r.raise_for_status()
            return r.json()

    async def recipe(
        self,
        name: str,
        params: dict[str, str],
        *,
        task: str = "",
        mode: str | None = None,
    ) -> dict[str, Any]:
        body = dict(params)
        body["mode"] = _resolve_mode(task, mode)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{self.base_url}/recipe/{name}", json=body)
            r.raise_for_status()
            return r.json()


_client: BrowserAgentClient | None = None


def get_browser_client() -> BrowserAgentClient:
    global _client
    if _client is None:
        _client = BrowserAgentClient()
    return _client


async def is_browser_agent_available() -> bool:
    try:
        await get_browser_client().health()
        return True
    except Exception as exc:
        logger.debug("Browser agent unavailable: %s", exc)
        return False