import json
import logging
import os
import time
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("friday.ollama")

HEALTH_TTL_SEC = 120.0
_GROQ_MODEL_PREFIXES = ("openai/", "llama", "groq/", "meta-llama/")


def _keep_alive() -> int | str:
    try:
        from config import settings
        raw = str(getattr(settings, "OLLAMA_KEEP_ALIVE", "-1") or "-1").strip()
    except Exception:
        raw = "-1"
    if raw in {"-1", "forever", "infinite"}:
        return -1
    return raw


def _num_ctx() -> int:
    try:
        from config import settings
        return max(512, int(getattr(settings, "OLLAMA_NUM_CTX", 2048) or 2048))
    except Exception:
        return 2048


def _gen_options(max_tokens: int, temperature: float) -> dict[str, Any]:
    return {
        "num_predict": max_tokens,
        "temperature": temperature,
        "num_ctx": _num_ctx(),
    }


def _ollama_url() -> str:
    try:
        from config import ollama_base_url
        return ollama_base_url()
    except Exception:
        return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def _default_model() -> str:
    try:
        from config import settings
        return (settings.OLLAMA_MODEL or settings.LLM_MODEL or "qwen3.5:4b").strip()
    except Exception:
        return os.getenv("OLLAMA_MODEL", "qwen3.5:4b")


def resolve_ollama_model(model: str | None) -> str:
    """Map leftover Groq model names onto the configured local Ollama model."""
    default = _default_model()
    name = (model or default).strip()
    if not name:
        return default
    lowered = name.lower()
    if any(lowered.startswith(prefix) for prefix in _GROQ_MODEL_PREFIXES) or "/" in name:
        return default
    return name


def _get_groq_key() -> str:
    try:
        from config import settings, use_ollama
        if use_ollama():
            return ""
        return settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
    except Exception:
        return os.getenv("GROQ_API_KEY", "")


class _ChatResult:
    """LangChain-compatible response object (content attribute)."""

    def __init__(self, content: str):
        self.content = content

    def __str__(self) -> str:
        return self.content


class OllamaChat:
    """Minimal ChatGroq-compatible wrapper over Ollama /api/chat."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 600,
        base_url: str | None = None,
    ):
        self.model = resolve_ollama_model(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = (base_url or _ollama_url()).rstrip("/")

    def invoke(self, messages: Any) -> _ChatResult:
        payload_messages = _langchain_to_ollama_messages(messages)
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": payload_messages,
                    "stream": False,
                    "think": False,
                    "keep_alive": _keep_alive(),
                    "options": _gen_options(self.max_tokens, self.temperature),
                },
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
            return _ChatResult((content or "").strip())


def _langchain_to_ollama_messages(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]

    converted: list[dict[str, Any]] = []
    for msg in messages:
        name = type(msg).__name__.lower()
        if "system" in name:
            role = "system"
        elif "ai" in name or "assistant" in name:
            role = "assistant"
        else:
            role = "user"
        content = getattr(msg, "content", msg)
        if isinstance(content, list):
            text_parts: list[str] = []
            images: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    text_parts.append(str(item))
                    continue
                if item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
                elif item.get("type") == "image_url":
                    url = (item.get("image_url") or {}).get("url") or ""
                    if "," in url:
                        images.append(url.split(",", 1)[1])
            entry: dict[str, Any] = {"role": role, "content": "\n".join(text_parts).strip()}
            if images:
                entry["images"] = images
            converted.append(entry)
        else:
            converted.append({"role": role, "content": str(content)})
    return converted


def get_chat_llm(
    *,
    temperature: float = 0.2,
    max_tokens: int = 600,
    model: str | None = None,
):
    """Return a chat model for the configured provider (Ollama or Groq)."""
    try:
        from config import settings, use_ollama, groq_api_key
        if use_ollama():
            return OllamaChat(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                base_url=settings.OLLAMA_URL,
            )
        from langchain_groq import ChatGroq
        return ChatGroq(
            temperature=temperature,
            model_name=model or settings.LLM_MODEL,
            groq_api_key=groq_api_key(),
            max_tokens=max_tokens,
        )
    except Exception:
        return OllamaChat(model=model, temperature=temperature, max_tokens=max_tokens)


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float = 180.0):
        self.base_url = (base_url or _ollama_url()).rstrip("/")
        self.timeout = timeout
        self._healthy: bool | None = None
        self._health_checked_at: float = 0.0
        self._available_models: list[str] = []
        self._verified_models: set[str] = set()
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def is_healthy(self) -> bool:
        try:
            from config import use_ollama
            skip_ram_gate = use_ollama()
        except Exception:
            skip_ram_gate = False
        if not skip_ram_gate:
            from brain.context_manager import is_resource_constrained
            if is_resource_constrained(ram_threshold=85.0):
                return False

        if self._healthy is not None and (time.time() - self._health_checked_at) < HEALTH_TTL_SEC:
            return self._healthy
        try:
            r = await self._get_client().get(f"{self.base_url}/api/tags", timeout=3.0)
            self._healthy = r.status_code == 200
            if self._healthy:
                data = r.json()
                self._available_models = [m["name"] for m in data.get("models", [])]
                logger.info("Ollama online. Models: %s", self._available_models)
        except Exception as e:
            logger.warning("Ollama unreachable: %s", e)
            self._healthy = False
        self._health_checked_at = time.time()
        return bool(self._healthy)

    async def list_models(self) -> list[str]:
        if not self._available_models:
            await self.is_healthy()
        return self._available_models

    def _model_installed(self, model: str, models: list[str]) -> bool:
        return any(
            m == model or m.startswith(model) or model.startswith(m.split(":")[0])
            for m in models
        )

    async def ensure_model(self, model: str) -> bool:
        if model in self._verified_models:
            return True
        models = await self.list_models()
        if self._model_installed(model, models):
            self._verified_models.add(model)
            return True
        logger.error("Ollama model %s is not installed. Available: %s", model, models)
        return False

    def complete_sync(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> str:
        model = resolve_ollama_model(model)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": _keep_alive(),
            "options": _gen_options(max_tokens, temperature),
        }
        if system:
            payload["system"] = system
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()

    async def complete(
        self, prompt: str, model: str | None = None,
        system: str | None = None, max_tokens: int = 800,
        temperature: float = 0.7, stream: bool = False, json_mode: bool = False,
    ) -> str:
        model = resolve_ollama_model(model)
        if not await self.is_healthy():
            logger.warning("Ollama down — attempting fallback.")
            return await self._groq_fallback(prompt, model, max_tokens)

        if not await self.ensure_model(model):
            return (
                f"Local model '{model}' is not installed in Ollama. "
                f"Available: {', '.join(self._available_models) or 'none'}."
            )

        payload: dict[str, Any] = {
            "model": model, "prompt": prompt, "stream": False, "think": False,
            "keep_alive": _keep_alive(),
            "options": _gen_options(max_tokens, temperature),
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
            logger.error("Ollama HTTP error: %s", e)
            if e.response.status_code >= 500:
                self._healthy = False
                self._health_checked_at = time.time()
            return await self._groq_fallback(prompt, model, max_tokens)
        except Exception as e:
            logger.error("Ollama complete error: %s", e)
            self._healthy = False
            self._health_checked_at = time.time()
            return await self._groq_fallback(prompt, model, max_tokens)

    async def stream_complete(
        self, prompt: str, model: str | None = None,
        system: str | None = None, max_tokens: int = 800, temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        model = resolve_ollama_model(model)
        if not await self.is_healthy():
            yield await self._groq_fallback(prompt, model, max_tokens)
            return

        payload = {
            "model": model, "prompt": prompt, "stream": True, "think": False,
            "keep_alive": _keep_alive(),
            "options": _gen_options(max_tokens, temperature),
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
            logger.error("Stream error: %s", e)
            yield f"[Stream error: {e}]"

    async def chat(
        self, messages: list[dict], model: str | None = None,
        system: str | None = None, max_tokens: int = 800,
        temperature: float = 0.7, tools: list | None = None,
    ) -> dict:
        model = resolve_ollama_model(model)
        if not await self.is_healthy():
            text = await self._groq_fallback(messages[-1]["content"] if messages else "", model, max_tokens)
            return {"content": text, "tool_calls": []}

        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False, "think": False,
            "keep_alive": _keep_alive(),
            "options": _gen_options(max_tokens, temperature),
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
            logger.error("Chat error: %s", e)
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
            logger.error("Embed error: %s", e)
            return []

    async def _groq_fallback(self, prompt: str, model: str, max_tokens: int, stream: bool = False) -> str:
        if not _get_groq_key():
            return "My local language model isn't available. Make sure Ollama is running."

        groq_model = "openai/gpt-oss-20b"

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
            logger.error("Groq fallback failed: %s", e)
            return "My language service isn't available right now."


async def ollama_complete(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 600,
    stream: bool = False,
    system: str | None = None,
    temperature: float = 0.7,
) -> str:
    """Primary local completion used by groq_complete when LLM_PROVIDER=ollama."""
    client = get_ollama()
    ollama_model = resolve_ollama_model(model)
    if not await client.is_healthy():
        logger.error("Ollama is not reachable at %s", client.base_url)
        return "My local language model isn't available. Make sure Ollama is running."

    if not await client.ensure_model(ollama_model):
        return (
            f"Local model '{ollama_model}' is not installed in Ollama. "
            f"Available: {', '.join(client._available_models) or 'none'}."
        )

    queue = None
    if stream:
        try:
            from services.runtime_state import current_stream_queue
            queue = current_stream_queue.get()
        except (ImportError, LookupError):
            queue = None

    payload: dict[str, Any] = {
        "model": ollama_model,
        "prompt": prompt,
        "stream": queue is not None,
        "think": False,
        "keep_alive": _keep_alive(),
        "options": _gen_options(max_tokens, temperature),
    }
    if system:
        payload["system"] = system

    try:
        if queue is not None:
            full_text: list[str] = []
            async with client._get_client().stream(
                "POST", f"{client.base_url}/api/generate", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("response") or ""
                    if token:
                        full_text.append(token)
                        queue.put_nowait(token)
                    if chunk.get("done"):
                        break
            client._verified_models.add(ollama_model)
            client._healthy = True
            client._health_checked_at = time.time()
            return "".join(full_text).strip()

        resp = await client._get_client().post(f"{client.base_url}/api/generate", json=payload)
        resp.raise_for_status()
        client._verified_models.add(ollama_model)
        client._healthy = True
        client._health_checked_at = time.time()
        return (resp.json().get("response") or "").strip()
    except Exception as exc:
        logger.error("Ollama completion failed: %s", exc)
        return "My local language model isn't available right now. Try that again in a moment."


_client: OllamaClient | None = None

def get_ollama() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
