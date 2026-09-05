"""
model_router.py — Hybrid model selection for FRIDAY.

Cloud Groq:
  Fast path (openai/gpt-oss-20b): jokes, time, open app, local music, volume, chat.
  Heavy path (openai/gpt-oss-120b): headlines, web search, browser work, planning.

Local Ollama: both paths use the installed Qwen3.5 4B model.
"""

from __future__ import annotations

from typing import Any

from brain.state import IntentCategory


def _load_models() -> tuple[str, str]:
    try:
        from config import settings, use_ollama

        if use_ollama():
            name = (settings.OLLAMA_MODEL or settings.LLM_MODEL or "qwen3.5:4b").strip()
            return name, name
        return (
            (settings.LLM_MODEL or "openai/gpt-oss-20b").strip(),
            (settings.LLM_MODEL_HEAVY or "openai/gpt-oss-120b").strip(),
        )
    except Exception:
        return "openai/gpt-oss-20b", "openai/gpt-oss-120b"


FAST_MODEL, HEAVY_MODEL = _load_models()

_HEAVY_INTENTS = frozenset({
    IntentCategory.NEWS,
    IntentCategory.SEARCH_WEB,
    IntentCategory.OPEN_YOUTUBE,
    IntentCategory.TAB_CLEANUP,
    IntentCategory.SCREEN_READ,
    IntentCategory.TRANSLATE,
    IntentCategory.REMINDER,
    IntentCategory.WEATHER,
    IntentCategory.EXPLAIN,
    IntentCategory.SUMMARISE,
    IntentCategory.CODE_HELP,
    IntentCategory.MULTI_STEP,
})

_LOCAL_MEDIA_HINTS = frozenset({
    "local",
    "garage",
    "computer",
    "pc",
    "library",
})


def resolve_llm_model(
    intent: IntentCategory | str | None,
    params: dict[str, Any] | None = None,
    *,
    cleaned_input: str = "",
    for_plan: bool = False,
    for_classify: bool = False,
) -> str:
    """Pick the LLM model for this turn."""
    if for_plan:
        return HEAVY_MODEL

    if for_classify:
        return FAST_MODEL

    if isinstance(intent, str):
        try:
            intent = IntentCategory(intent)
        except ValueError:
            intent = IntentCategory.CHAT

    params = params or {}
    text = (cleaned_input or "").lower()

    if intent in _HEAVY_INTENTS:
        return HEAVY_MODEL

    if intent == IntentCategory.PLAY_MEDIA:
        platform = str(params.get("platform") or "").lower()
        song = str(params.get("song") or "").lower()
        if platform in ("local", "") and (
            not song
            or any(h in song for h in _LOCAL_MEDIA_HINTS)
            or "garage" in text
        ):
            return FAST_MODEL
        if platform in ("youtube", "youtube_music", "spotify"):
            return FAST_MODEL

    if intent == IntentCategory.CHAT and len(text.split()) > 28:
        return HEAVY_MODEL

    return FAST_MODEL