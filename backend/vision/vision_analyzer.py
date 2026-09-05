"""
vision_analyzer.py — Screen Analysis Engine
============================================
Analyzes screenshots using Groq Vision API or local Ollama moondream.
Auto-detects provider and falls back gracefully.
"""

import os
import sys
import time
import httpx

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings

_ollama_cache: dict = {"ts": 0.0, "available": False}
_OLLAMA_CACHE_TTL = 30.0
_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=25.0)
    return _http_client

_VISION_PROMPT = (
    "Describe what is visible on this computer screen. "
    "List any open applications, text content, buttons, and UI elements. "
    "Be concise but thorough."
)


def is_ollama_available() -> bool:
    """Check if Ollama is running at the configured host (cached 30s)."""
    from config import use_ollama
    if not use_ollama():
        from brain.context_manager import is_resource_constrained
        if is_resource_constrained(ram_threshold=85.0):
            return False

    now = time.time()
    if now - _ollama_cache["ts"] < _OLLAMA_CACHE_TTL:
        return _ollama_cache["available"]

    host = getattr(settings, "OLLAMA_URL", None) or getattr(settings, "OLLAMA_HOST", "http://127.0.0.1:11434")
    available = False
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=2.5)
        available = resp.status_code == 200
    except Exception:
        available = False
    _ollama_cache.update(ts=now, available=available)
    return available


def analyze_screen(image_base64: str) -> str:
    """
    Analyze a base64-encoded screenshot.
    Tries the configured provider, falls back to alternatives.
    Returns a natural language description of the screen.
    """
    if not image_base64:
        return "No image data provided."

    from config import use_ollama

    provider = getattr(settings, "VISION_PROVIDER", None)
    if not provider:
        provider = "ollama" if use_ollama() else "groq"

    # Auto mode: try ollama first if available, else groq
    if provider == "auto":
        if is_ollama_available():
            result = _analyze_ollama(image_base64)
            if result:
                return result
        return _analyze_groq(image_base64) or "Screen analysis unavailable."

    if provider == "ollama":
        result = _analyze_ollama(image_base64)
        if result:
            return result
        # Fallback to groq
        return _analyze_groq(image_base64) or "Screen analysis unavailable."

    # Default: groq
    result = _analyze_groq(image_base64)
    if result:
        return result
    # Fallback to ollama
    result = _analyze_ollama(image_base64)
    return result or "Screen analysis unavailable."


def analyze_screen_from_file(filepath: str) -> str:
    """Read an image file and analyze it."""
    import base64
    try:
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return analyze_screen(b64)
    except Exception as e:
        return f"Failed to read image file: {e}"


def _analyze_groq(image_base64: str) -> str:
    """Analyze screenshot via Groq Vision API."""
    api_key = settings.GROQ_API_KEY
    if not api_key:
        return ""

    try:
        payload = {
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 512,
            "temperature": 0.3,
        }

        resp = _get_http_client().post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=20.0,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[VisionAnalyzer] Groq returned {resp.status_code}: {resp.text[:200]}")
        return ""

    except Exception as e:
        print(f"[VisionAnalyzer] Groq vision error: {e}")
        return ""


def _analyze_ollama(image_base64: str) -> str:
    """Analyze screenshot via the local Ollama model."""
    host = getattr(settings, "OLLAMA_URL", None) or getattr(settings, "OLLAMA_HOST", "http://127.0.0.1:11434")
    models = []
    local_model = getattr(settings, "OLLAMA_MODEL", "") or ""
    if local_model:
        models.append(local_model)
    models.append("moondream:latest")

    for model in models:
        try:
            payload = {
                "model": model,
                "prompt": _VISION_PROMPT,
                "images": [image_base64],
                "stream": False,
            }
            resp = httpx.post(f"{host}/api/generate", json=payload, timeout=40.0)
            if resp.status_code == 200:
                text = (resp.json().get("response") or "").strip()
                if text:
                    return text
            print(f"[VisionAnalyzer] Ollama {model} returned {resp.status_code}")
        except Exception as e:
            print(f"[VisionAnalyzer] Ollama vision error ({model}): {e}")
    return ""
