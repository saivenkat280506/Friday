"""
browser_agent.py — LangChain DOM-based browser agent (no vision).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from brain.browser_knowledge import get_browser_knowledge
from brain.browser_session_db import format_actions_for_memory, get_browser_session_db
from config import settings
from executor.browser_agent_client import _resolve_mode, get_browser_client, is_browser_agent_available

logger = logging.getLogger("friday.browser_agent")

MAX_STEPS = 15

SYSTEM_TEMPLATE = """You control a real Chrome browser via structured commands. You do NOT see screenshots.
You receive DOM JSON (url, title, interactive elements with selectors).

{knowledge}

Reply with EXACTLY ONE command per turn:
GOTO(url) | SEARCH(query) | CLICK(selector) | TYPE(selector, "text")
SCROLL(up|down, n) | PRESS(key) | WAIT(seconds) | DONE("summary")

Rules:
- Prefer selectors from the interactive list.
- Use SEARCH for new queries; CLICK to follow links or play buttons.
- End with DONE("...") when the task is complete.
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


async def run_browser_agent(task: str, *, mode: str | None = None, max_steps: int = MAX_STEPS) -> tuple[bool, str]:
    """Run the LangChain + DOM observe-act loop."""
    if not await is_browser_agent_available():
        return False, "Browser agent service is not running."

    client = get_browser_client()
    resolved_mode = _resolve_mode(task, mode)
    db = get_browser_session_db()
    session_id = await db.start_session(task, resolved_mode)
    knowledge = get_browser_knowledge()

    await client.start_session(resolved_mode)
    llm = ChatGroq(
        model_name=settings.LLM_MODEL or "llama-3.3-70b-versatile",
        temperature=0.1,
        max_tokens=256,
        groq_api_key=settings.GROQ_API_KEY or None,
    )

    messages: list[Any] = []
    action_log: list[dict[str, Any]] = []

    try:
        for step in range(1, max_steps + 1):
            obs = await client.observe()
            state = obs.get("state") or {}
            context = knowledge.build_agent_context(task, state)
            system = SYSTEM_TEMPLATE.format(knowledge=context)
            user = (
                f"Task: {task}\nStep {step}/{max_steps}\n"
                f"Page state:\n{_format_page_state(state)}\n"
                f"Recent actions: {json.dumps(action_log[-3:])}\n"
                "Next command:"
            )

            if not messages:
                messages = [SystemMessage(content=system)]
            messages.append(HumanMessage(content=user))

            response = await asyncio.to_thread(llm.invoke, messages)
            cmd_text = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_command(cmd_text)
            messages.append(response)

            if parsed.get("action") == "DONE":
                summary = parsed.get("summary", "Task completed.")
                await db.end_session(session_id, "success", summary)
                _store_episode(task, action_log, "success", state)
                return True, summary

            payload = {k: v for k, v in parsed.items() if k != "action"}
            payload["action"] = parsed.get("action", "UNKNOWN")
            result = await client.action(payload, mode=resolved_mode)
            action_log.append({"step": step, "command": cmd_text, "result": result.get("message")})
            await db.log_action(
                session_id,
                step,
                cmd_text,
                selector=parsed.get("selector"),
                result=result.get("message"),
                dom_url=state.get("url"),
            )

            if not result.get("success"):
                await db.end_session(session_id, "failed", result.get("message", "Action failed"))
                return False, result.get("message", "Browser action failed")

            if parsed.get("action") == "UNKNOWN":
                await db.end_session(session_id, "failed", f"Unparsed command: {cmd_text}")
                return False, f"Could not parse agent command: {cmd_text}"

        await db.end_session(session_id, "partial", "Reached max steps")
        return True, "Reached maximum browser agent steps."
    except Exception as exc:
        logger.exception("Browser agent error")
        await db.end_session(session_id, "error", str(exc))
        return False, f"Browser agent error: {exc}"


def _store_episode(task: str, actions: list[dict], outcome: str, state: dict) -> None:
    try:
        from brain.memory_store import get_memory_store
        store = get_memory_store()
        if store.is_ready:
            store.store_browser_episode(
                task=task,
                actions=format_actions_for_memory(actions),
                outcome=outcome,
                dom_summary=f"{state.get('url', '')} | {state.get('title', '')}",
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
    if result.get("success"):
        return True, result.get("message", "Done")
    return False, result.get("message", "Recipe failed")