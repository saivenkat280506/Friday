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

DEFAULT_MODEL = "openai/gpt-oss-20b"
_MODEL_ALIASES = {
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama3.1": "openai/gpt-oss-20b",
    "llama3.3": "openai/gpt-oss-120b",
}


def resolve_groq_model(model: str | None) -> str:
    name = (model or DEFAULT_MODEL).strip()
    return _MODEL_ALIASES.get(name, name) or DEFAULT_MODEL


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
    try:
        from config import use_ollama
        if use_ollama():
            from brain.ollama_client import ollama_complete
            return await ollama_complete(
                prompt, model=model, max_tokens=max_tokens, stream=stream
            )
    except Exception as exc:
        logger.error("Local Ollama dispatch failed: %s", exc)
        return "My local language model isn't available right now. Make sure Ollama is running."

    key = _api_key()
    if not key:
        logger.error("GROQ_API_KEY is missing from backend/.env")
        return "My language service isn't configured. Add a Groq API key, then try again."

    groq_model = resolve_groq_model(model)
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
            err = str(exc).lower()
            if ("404" in err or "decommissioned" in err or "not found" in err or "model" in err) and groq_model != DEFAULT_MODEL:
                logger.warning("Groq model %s failed (%s) — retrying %s", groq_model, exc, DEFAULT_MODEL)
                groq_model = DEFAULT_MODEL
                continue
            if _is_rate_limited(exc) and attempt < 2:
                delay = 1.5 * (attempt + 1)
                logger.warning("Groq rate limited — retry in %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            logger.error("Groq completion failed: %s", exc)
            return "My language service isn't available right now. Try that again in a moment."

    logger.error("Groq completion failed after retries: %s", last_exc)
    return "My language service isn't available right now. Try that again in a moment."