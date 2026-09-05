"""
browser_agent.py — LangChain DOM browser agent with human-like Puppeteer sidecar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from brain.browser_knowledge import get_browser_knowledge
from brain.browser_session_db import format_actions_for_memory, get_browser_session_db
from config import settings
from executor.browser_agent_client import _resolve_mode, get_browser_client, is_browser_agent_available

logger = logging.getLogger("friday.browser_agent")

MAX_STEPS = 15

SYSTEM_TEMPLATE = """You are F.R.I.D.A.Y.'s Puppeteer web automation specialist.
Execute web tasks with human-like, same-page research. The Node sidecar uses stealth Chrome,
ghost-cursor clicks, human typing (delays + typos), and reading-scroll pauses.

{knowledge}

Reply with EXACTLY ONE command per turn:
GOTO(url) | SEARCH(query) | CLICK(selector) | TYPE(selector, "text")
SCROLL(up|down, n) | PRESS(key) | WAIT(seconds) | CLOSETABS | DONE("summary")

Rules:
- Prefer staying on the same tab; use CLOSETABS if pop-ups appear.
- Use data-testid / aria-label selectors from the interactive list.
- Spotify: search exact title/artist; use recipes when possible.
- ChatGPT: TYPE into prompt box, PRESS("Enter") or click send.
- News/research: SEARCH with engine news or scroll results on one page.
- End with DONE("voice-friendly summary") when complete.
- Output only the raw command, no markdown.
"""


def _parse_command(text: str) -> dict[str, Any]:
    cmd = text.strip().replace("```", "").split("\n")[0].strip()
    if cmd.upper().startswith("DONE"):
        m = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
        return {"action": "DONE", "summary": m.group(1) if m else "Task completed."}

    if cmd.upper().startswith("GOTO"):
        m = re.search(r'GOTO\("(.+?)"\)', cmd)
        return {"action": "GOTO", "url": m.group(1) if m else ""}

    if cmd.upper().startswith("SEARCH"):
        m = re.search(r'SEARCH\("(.+?)"\)', cmd)
        return {"action": "SEARCH", "query": m.group(1) if m else ""}

    if cmd.upper().startswith("CLICK"):
        m = re.search(r'CLICK\("(.+?)"\)', cmd)
        return {"action": "CLICK", "selector": m.group(1) if m else ""}

    if cmd.upper().startswith("TYPE"):
        m = re.search(r'TYPE\("(.+?)",\s*"(.+?)"\)', cmd)
        if m:
            return {"action": "TYPE", "selector": m.group(1), "text": m.group(2)}

    if cmd.upper().startswith("SCROLL"):
        m = re.search(r"SCROLL\((up|down),\s*(\d+)\)", cmd, re.I)
        if m:
            return {"action": "SCROLL", "direction": m.group(1).lower(), "amount": int(m.group(2))}

    if cmd.upper().startswith("PRESS"):
        m = re.search(r'PRESS\("(.+?)"\)', cmd)
        return {"action": "PRESS", "key": m.group(1) if m else "Enter"}

    if cmd.upper().startswith("WAIT"):
        m = re.search(r"WAIT\((\d+(?:\.\d+)?)\)", cmd)
        return {"action": "WAIT", "seconds": float(m.group(1)) if m else 1.0}

    if cmd.upper().startswith("CLOSETABS"):
        return {"action": "CLOSETABS"}

    return {"action": "UNKNOWN", "raw": cmd}


def _format_page_state(state: dict[str, Any]) -> str:
    interactive = state.get("interactive", [])[:25]
    slim = {
        "url": state.get("url"),
        "title": state.get("title"),
        "media": state.get("media"),
        "interactive": interactive,
    }
    return json.dumps(slim, ensure_ascii=False)[:6000]


def _observation_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("observation") or {}


async def _vision_hint(observation: dict[str, Any], task: str) -> str:
    """Optional vision fallback when DOM is ambiguous."""
    b64 = observation.get("screenshot_base64")
    if not b64:
        return ""
    try:
        from vision.vision_analyzer import analyze_screen

        hint = await asyncio.to_thread(
            analyze_screen,
            b64,
        )
        return (hint or "")[:1200]
    except Exception as exc:
        logger.debug("Vision hint skipped: %s", exc)
        return ""


async def _speak(message: str) -> None:
    if not message:
        return
    import os

    if os.getenv("FRIDAY_BROWSER_TEST", "").strip().lower() in {"1", "true", "yes"}:
        return
    try:
        from tts.hybrid_tts import speak_hybrid

        await speak_hybrid(message[:280])
    except Exception as exc:
        logger.debug("TTS announce skipped: %s", exc)


def _action_voice_line(action: str, parsed: dict[str, Any], observation: dict[str, Any]) -> str:
    """Short TTS line for major browser steps (master prompt transparency)."""
    if action == "GOTO":
        url = parsed.get("url") or observation.get("current_url") or "the page"
        if "spotify" in str(url).lower():
            return "Opening Spotify web player..."
        if "chatgpt" in str(url).lower():
            return "Opening ChatGPT..."
        if "news" in str(url).lower():
            return "Opening Google News..."
        return f"Navigating to {url}..."
    if action == "SEARCH":
        query = parsed.get("query") or ""
        engine = (parsed.get("engine") or "google").lower()
        if engine == "news":
            return f"Researching news about {query}..."
        return f"Searching the web for {query}..."
    if action == "TYPE":
        return "Typing your request into the page..."
    if action == "CLICK":
        return "Clicking the next element like a human would..."
    if action == "SCROLL":
        return "Scrolling through results with reading pauses..."
    return observation.get("voice_message") or "Continuing in the browser..."

def _clean_topic(task: str, *noise_words: str) -> str:
    cleaned = task
    for word in noise_words:
        cleaned = re.sub(rf"(?i)\b{re.escape(word)}\b", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.-") or task


def _route_recipe(task: str) -> tuple[str, dict[str, str]] | None:
    lower = task.lower()
    if "chatgpt" in lower or "chat gpt" in lower:
        if ":" in task:
            prompt = task.split(":", 1)[-1].strip()
        else:
            prompt = _clean_topic(task, "chatgpt", "chat", "gpt", "send", "to", "ask")
        return "sendChatGptPrompt", {"prompt": prompt or task}
    if "spotify" in lower and "volume" in lower:
        m = re.search(r"(\d+)\s*%?", task)
        vol = m.group(1) if m else "40"
        return "setSpotifyVolume", {"volume": vol}
    if "spotify" in lower or ("play" in lower and "song" in lower):
        song = _clean_topic(task, "play", "on", "spotify", "song", "the")
        return "playSpotify", {"song": song or task}
    if "news" in lower or "headlines" in lower:
        topic = _clean_topic(task, "research", "news", "headlines", "about", "find", "get")
        return "researchNews", {"topic": topic or task}
    return None


async def run_browser_agent(
    task: str,
    *,
    mode: str | None = None,
    max_steps: int = MAX_STEPS,
) -> tuple[bool, str]:
    """Run the LangChain + human Puppeteer observe-act loop."""
    if not await is_browser_agent_available():
        return False, "Browser agent service is not running. Start FRIDAY backend to launch the sidecar."

    client = get_browser_client()
    resolved_mode = _resolve_mode(task, mode)
    db = get_browser_session_db()
    session_id = await db.start_session(task, resolved_mode)
    knowledge = get_browser_knowledge()

    routed = _route_recipe(task)
    if routed:
        recipe_name, params = routed
        result = await client.recipe(recipe_name, params, task=task, mode=resolved_mode)
        obs = _observation_from_result(result)
        voice = obs.get("voice_message") or result.get("message", "Recipe finished.")
        await _speak(voice)
        if result.get("success"):
            await db.end_session(session_id, "success", voice)
            return True, voice
        await db.end_session(session_id, "failed", result.get("message", "Recipe failed"))
        return False, result.get("message", "Recipe failed")

    await client.start_session(resolved_mode)
    from brain.ollama_client import get_chat_llm

    llm = get_chat_llm(
        temperature=0.1,
        max_tokens=256,
        model=settings.LLM_MODEL,
    )

    messages: list[Any] = []
    action_log: list[dict[str, Any]] = []
    last_observation: dict[str, Any] = {}

    try:
        for step in range(1, max_steps + 1):
            obs_resp = await client.observe()
            observation = obs_resp.get("observation") or {}
            state = observation.get("state") or obs_resp.get("state") or {}
            last_observation = observation

            context = knowledge.build_agent_context(task, state)
            system = SYSTEM_TEMPLATE.format(knowledge=context)
            vision_note = ""
            if step > 1 and not state.get("interactive"):
                vision_note = await _vision_hint(observation, task)
            user = (
                f"Task: {task}\nStep {step}/{max_steps}\n"
                f"Page state:\n{_format_page_state(state)}\n"
                f"Recent actions: {json.dumps(action_log[-3:])}\n"
            )
            if vision_note:
                user += f"Vision hint: {vision_note}\n"
            user += "Next command:"

            if not messages:
                messages = [SystemMessage(content=system)]
            messages.append(HumanMessage(content=user))

            response = await asyncio.to_thread(llm.invoke, messages)
            cmd_text = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_command(cmd_text)
            messages.append(response)

            if parsed.get("action") == "DONE":
                summary = parsed.get("summary", "Task completed.")
                voice = observation.get("voice_message") or summary
                await _speak(voice)
                await db.end_session(session_id, "success", summary)
                _store_episode(task, action_log, "success", state, observation)
                return True, voice

            payload = {k: v for k, v in parsed.items() if k != "action"}
            payload["action"] = parsed.get("action", "UNKNOWN")
            result = await client.action(payload, mode=resolved_mode)
            observation = _observation_from_result(result) or observation
            last_observation = observation
            action_log.append(
                {
                    "step": step,
                    "command": cmd_text,
                    "result": result.get("message"),
                    "url": observation.get("current_url"),
                }
            )
            await db.log_action(
                session_id,
                step,
                cmd_text,
                selector=parsed.get("selector"),
                result=result.get("message"),
                dom_url=state.get("url"),
            )

            if not result.get("success"):
                fail_msg = observation.get("voice_message") or result.get("message", "Action failed")
                await _speak(fail_msg)
                await db.end_session(session_id, "failed", fail_msg)
                return False, fail_msg

            action_name = str(parsed.get("action") or "").upper()
            if action_name in {"GOTO", "SEARCH", "TYPE", "CLICK", "SCROLL"}:
                await _speak(_action_voice_line(action_name, parsed, observation))

            if parsed.get("action") == "UNKNOWN":
                await db.end_session(session_id, "failed", f"Unparsed command: {cmd_text}")
                return False, f"Could not parse agent command: {cmd_text}"

        partial = last_observation.get("voice_message") or "Reached maximum browser agent steps."
        await db.end_session(session_id, "partial", partial)
        return True, partial
    except Exception as exc:
        logger.exception("Browser agent error")
        await db.end_session(session_id, "error", str(exc))
        return False, f"Browser agent error: {exc}"


def _store_episode(
    task: str,
    actions: list[dict],
    outcome: str,
    state: dict,
    observation: dict | None = None,
) -> None:
    try:
        from brain.memory_store import get_memory_store

        store = get_memory_store()
        if store.is_ready:
            dom_summary = f"{state.get('url', '')} | {state.get('title', '')}"
            if observation:
                dom_summary += f" | {observation.get('voice_message', '')[:120]}"
            store.store_browser_episode(
                task=task,
                actions=format_actions_for_memory(actions),
                outcome=outcome,
                dom_summary=dom_summary,
            )
    except Exception as exc:
        logger.debug("Episode store skipped: %s", exc)


async def run_browser_recipe(
    recipe: str,
    params: dict[str, str],
    *,
    task: str = "",
    mode: str | None = None,
) -> tuple[bool, str]:
    if not await is_browser_agent_available():
        return False, "Browser agent service is not running."
    client = get_browser_client()
    result = await client.recipe(recipe, params, task=task or recipe, mode=mode)
    obs = _observation_from_result(result)
    voice = obs.get("voice_message") or result.get("message", "Done")
    if result.get("success"):
        await _speak(voice)
        return True, voice
    return False, result.get("message", "Recipe failed")