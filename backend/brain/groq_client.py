"""
groq_client.py — Groq-only LLM completions for FRIDAY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger("friday.groq")

DEFAULT_MODEL = "llama-3.1-8b-instant"


def _api_key() -> str:
    try:
        from config import settings
        return settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
    except Exception:
        return os.getenv("GROQ_API_KEY", "")


def _is_rate_limited(exc: Exception) -> bool:
    err = str(exc).lower()
    return "429" in err or "rate limit" in err or "too many requests" in err


async def groq_complete(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 600,
    stream: bool = False,
) -> str:
    key = _api_key()
    if not key:
        return "[Groq API key not configured]"

    groq_model = model if model and str(model).startswith("llama") else DEFAULT_MODEL
    queue = None
    if stream:
        try:
            from services.runtime_state import current_stream_queue
            queue = current_stream_queue.get()
        except (ImportError, LookupError):
            queue = None

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if queue is not None:
                    full_text: list[str] = []
                    async with client.stream(
                        "POST",
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": groq_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": max_tokens,
                            "stream": True,
                        },
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                            if delta:
                                full_text.append(delta)
                                queue.put_nowait(delta)
                    return "".join(full_text).strip()

                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": groq_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            last_exc = exc
            if _is_rate_limited(exc) and attempt < 2:
                delay = 1.5 * (attempt + 1)
                logger.warning("Groq rate limited — retry in %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            logger.error("Groq completion failed: %s", exc)
            return f"[Groq unavailable: {exc}]"

    logger.error("Groq completion failed after retries: %s", last_exc)
    return f"[Groq unavailable: {last_exc}]"