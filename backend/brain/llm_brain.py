"""
llm_brain.py — Fast intent router via Groq groq/compound-mini
==============================================================
Intent classification only (low latency). Long-form chat/QA uses
groq/compound-mini in command_processor._groq_generate as well.
Reserve 70B only for rare heavy reasoning if ever needed.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from config import active_llm_model

ROUTER_MODEL = os.environ.get("FRIDAY_ROUTER_MODEL") or active_llm_model()


def _router_llm():
    from brain.ollama_client import get_chat_llm
    return get_chat_llm(temperature=0, max_tokens=120, model=ROUTER_MODEL)

# Compact prompt → fewer tokens → lower latency
SYSTEM_PROMPT = """You map user text to ONE FRIDAY tool intent. Return ONLY JSON:
{"intent":"...","parameters":{...}}

BROWSER (Puppeteer — preferred for web/music UI):
- play_youtube_music {"song":"..."} — ONLY if user explicitly asks to play a song/music (e.g., "play X")
- play_youtube_search {"song":"..."} — same as music (legacy)
- browser_scroll_test {} — "scroll test" / "scroll speed test"
- search_browser {"query":"..."} — ONLY explicit "search for X" / "google X" (opens browser beside Friday + spoken summary)
- browser_navigate {"url":"https://..."}
- browser_click {"selector":"..."} or {"text":"..."}
- browser_type {"selector":"...","text":"..."}
- browser_scroll {"pixels":350,"times":3,"delayMs":5000}
- browser_action {"action":"youtube_music_play|scroll_test|web_search|..."}
- linkedin_browser_demo {} — scroll then YouTube Music demo
- play_spotify {"song":"..."} — only if user says Spotify
- spotify_login {} — only if user says log in to Spotify

OTHER:
- open_app {"app":"..."}
- send_whatsapp {"name":"...","message":"..."} — use the spoken contact name only. NEVER invent a phone number. NEVER output placeholders such as "+91...". Omit number unless the user said a full real number. NEVER use name "all", "everyone", or a group. Sends immediately; do not draft.
- send_whatsapp_all {"message":"..."} — ONLY if the user said "all contacts" / "every contact". Never groups, never everyone on WhatsApp. Message required.
- remember {"text":"..."} — only if user said remember/note/save this
- recall {"query":"..."} — only "what do you remember" / "do you remember" / "what did I tell you" / "what is my X". NEVER for general "what is mitochondria" or "who is Donald Trump"
- add_task {"title":"..."} / list_tasks {} / complete_task {"query":"..."}
- time {"timezone":"Asia/Tokyo"} — "time in Tokyo", "Tokyo time", "what time is it"
- qa {"query":"..."} — general knowledge: "what is X", "who is X", "explain X", "tell me about X"
- smart_search {"query":"..."} — "about X on the internet", "look up X" (same split-screen search briefing)
- confirm_whatsapp_send {} — user said yes/ok/send after a draft
- cancel_whatsapp_send {} — user said no/cancel after a draft
- play_local_music {} — "play music" / "play garage music" (local garage track)
- daddys_home {} — "wake up daddy's home" / "daddy's home"
- music_control / volume_control
- read_headlines {"query":"..."} / smart_search {"query":"..."}  (text only, no browser)
- chat / joke / intro / qa / cancel_task / web_agent

Rules: ONLY select play_youtube_music if the user explicitly asks to play music or a song (e.g. "play X"). NEVER select play_youtube_music for general statements, comments, or remarks (e.g. "this was the command code") — route those to "chat" with {"response":""}. No markdown.
"""


def decide_action(user_input: str, context: str = ""):
    # Keep context tiny for speed
    ctx = (context or "")[-400:]
    prompt = f"{SYSTEM_PROMPT}\nContext:{ctx}\nUser:{user_input}\nJSON:"
    try:
        response = _router_llm().invoke(prompt)
        content = (response.content or "").strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        # Extract first {...} if model adds chatter
        if "{" in content and "}" in content:
            content = content[content.index("{") : content.rindex("}") + 1]
        data = json.loads(content)
        if not isinstance(data, dict) or "intent" not in data:
            raise ValueError("bad shape")
        data.setdefault("parameters", {})
        if not isinstance(data["parameters"], dict):
            data["parameters"] = {}
        print(f"[LLM Brain] {ROUTER_MODEL} -> {data.get('intent')}")
        return data
    except Exception as e:
        print(f"[LLM Brain Error] {e}")
        # Safe browser-oriented fallback for ambiguous web-ish input
        low = user_input.lower()
        if "play" in low and (
            "youtube" in low or "yotube" in low or "utube" in low or "yt music" in low
            or low.startswith("play ") or "play " in low
        ):
            # Extract after last "play "
            song = user_input
            if "play " in low:
                song = user_input[low.index("play ") + 5 :].strip()
            song = re.sub(
                r"\s+on\s+(?:you\s*tube|youtube|yotube|utube|yt)(?:\s*music)?\s*$",
                "",
                song,
                flags=re.I,
            ).strip(" .,!?")
            if song:
                return {"intent": "play_youtube_music", "parameters": {"song": song}}
        if re.search(r"\b(?:what is|what are|who is|who are|tell me about|explain)\b", low):
            return {"intent": "qa", "parameters": {"query": user_input}}
        if "on the internet" in low or "on the web" in low or low.startswith("look up "):
            return {"intent": "smart_search", "parameters": {"query": user_input}}
        if "search" in low or "google" in low:
            return {"intent": "search_browser", "parameters": {"query": user_input}}
        return {"intent": "chat", "parameters": {"response": ""}}
