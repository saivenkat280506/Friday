import asyncio
import json
import logging
import os
import time
from typing import AsyncIterator, Any

import httpx

logger = logging.getLogger("friday.ollama")

OLLAMA_BASE = os.getenv("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
HEALTH_TTL_SEC = 45.0


def _get_groq_key() -> str:
    try:
        from config import settings
        return settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
    except Exception:
        return os.getenv("GROQ_API_KEY", "")


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._healthy: bool | None = None
        self._health_checked_at: float = 0.0
        self._available_models: list[str] = []
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def is_healthy(self) -> bool:
        from brain.context_manager import is_resource_constrained
        if is_resource_constrained(ram_threshold=85.0):
            return False

        if self._healthy is not None and (time.time() - self._health_checked_at) < HEALTH_TTL_SEC:
            return self._healthy
        try:
            r = await self._get_client().get(f"{self.base_url}/api/tags", timeout=3.0)
            self._healthy = (r.status_code == 200)
            if self._healthy:
                data = r.json()
                self._available_models = [m["name"] for m in data.get("models", [])]
                logger.info(f"Ollama online. Models: {self._available_models}")
        except Exception as e:
            logger.warning(f"Ollama unreachable: {e}")
            self._healthy = False
        self._health_checked_at = time.time()
        return self._healthy

    async def list_models(self) -> list[str]:
        if not self._available_models:
            await self.is_healthy()
        return self._available_models

    async def ensure_model(self, model: str) -> bool:
        models = await self.list_models()
        if any(m.startswith(model) for m in models):
            return True
        logger.info(f"Pulling model {model} ...")
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", f"{self.base_url}/api/pull", json={"name": model}) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            data = json.loads(line)
                            if data.get("status") == "success":
                                logger.info(f"Model {model} ready.")
                                self._available_models.append(model)
                                return True
        except Exception as e:
            logger.error(f"Pull failed: {e}")
        return False

    async def complete(
        self, prompt: str, model: str = DEFAULT_MODEL,
        system: str | None = None, max_tokens: int = 800,
        temperature: float = 0.7, stream: bool = False, json_mode: bool = False,
    ) -> str:
        if not await self.is_healthy():
            logger.warning("Ollama down — falling back to Groq.")
            return await self._groq_fallback(prompt, model, max_tokens)

        await self.ensure_model(model)

        payload: dict[str, Any] = {
            "model": model, "prompt": prompt, "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        try:
            resp = await self._get_client().post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except httpx.TimeoutException:
            logger.error("Ollama request timed out.")
            self._healthy = False
            self._health_checked_at = time.time()
            return await self._groq_fallback(prompt, model, max_tokens)
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e}")
            if e.response.status_code >= 500:
                self._healthy = False
                self._health_checked_at = time.time()
            return await self._groq_fallback(prompt, model, max_tokens)
        except Exception as e:
            logger.error(f"Ollama complete error: {e}")
            self._healthy = False
            self._health_checked_at = time.time()
            return await self._groq_fallback(prompt, model, max_tokens)

    async def stream_complete(
        self, prompt: str, model: str = DEFAULT_MODEL,
        system: str | None = None, max_tokens: int = 800, temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        if not await self.is_healthy():
            yield await self._groq_fallback(prompt, model, max_tokens)
            return

        payload = {
            "model": model, "prompt": prompt, "stream": True,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if system:
            payload["system"] = system

        try:
            async with self._get_client().stream("POST", f"{self.base_url}/api/generate", json=payload) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"[Stream error: {e}]"

    async def chat(
        self, messages: list[dict], model: str = DEFAULT_MODEL,
        system: str | None = None, max_tokens: int = 800,
        temperature: float = 0.7, tools: list | None = None,
    ) -> dict:
        if not await self.is_healthy():
            text = await self._groq_fallback(messages[-1]["content"] if messages else "", model, max_tokens)
            return {"content": text, "tool_calls": []}

        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        try:
            resp = await self._get_client().post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            message = resp.json().get("message", {})
            return {
                "content": message.get("content", "").strip(),
                "tool_calls": message.get("tool_calls", []),
            }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            return {"content": f"[Error: {e}]", "tool_calls": []}

    async def embed(self, text: str, model: str = "nomic-embed-text") -> list[float]:
        if not await self.is_healthy():
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.base_url}/api/embeddings", json={"model": model, "prompt": text})
                resp.raise_for_status()
                return resp.json().get("embedding", [])
        except Exception as e:
            logger.error(f"Embed error: {e}")
            return []

    async def _groq_fallback(self, prompt: str, model: str, max_tokens: int, stream: bool = False) -> str:
        if not _get_groq_key():
            return "[Ollama unavailable, Groq key not configured]"
        groq_model = "llama-3.1-8b-instant"

        queue = None
        if stream:
            try:
                from services.runtime_state import current_stream_queue
                queue = current_stream_queue.get()
            except (ImportError, LookupError):
                queue = None

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if queue is not None:
                    full_text: list[str] = []
                    async with client.stream(
                        "POST",
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {_get_groq_key()}", "Content-Type": "application/json"},
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
                    headers={"Authorization": f"Bearer {_get_groq_key()}", "Content-Type": "application/json"},
                    json={"model": groq_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Groq fallback failed: {e}")
            return "[Both Ollama and Groq unavailable]"


_client: OllamaClient | None = None

def get_ollama() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
