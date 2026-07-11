"""
groq_decision.py — Groq LLM tool-selection for the vision agent loop
=====================================================================
Chooses the next sync tool to invoke given a screen description and user intent.

Pipeline
--------
1. Rule-based fast path (no API call when confidence ≥ threshold).
2. Build prompt from ``ToolRegistry.get_tools_for_llm()`` + skills metadata.
3. Call Groq Llama-3.3-70B with structured selection rules.
4. Parse + validate JSON; enrich vague parameters.
5. On failure → rule-based fallback from user_intent keywords.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from groq import Groq

from brain.friday_persona import build_tool_system_prompt
from config import settings
from executor.tools_registry import get_tool_registry, load_skills_registry

logger = logging.getLogger("friday.groq_decision")

api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
client = Groq(api_key=api_key) if api_key else None

# Skip Groq API when rule match confidence meets this bar.
RULE_SKIP_LLM_THRESHOLD = 0.85

_VAGUE_TERMS = frozenset({
    "any article",
    "any page",
    "anything",
    "something",
    "whatever",
    "random page",
    "random article",
    "some article",
    "a page",
    "stuff",
})

_REWRITE_TOPICS = [
    "latest technology news today",
    "popular science discoveries",
    "artificial intelligence breakthroughs",
    "space exploration updates",
    "world news headlines",
    "healthy cooking recipes",
    "productivity tips for remote work",
]


@dataclass
class ToolDecision:
    """Structured tool-selection result."""

    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    source: str = "unknown"
    action: str | None = None  # "none" when no tool needed

    def to_json(self) -> str:
        if self.action == "none":
            return json.dumps({"action": "none", "confidence": self.confidence, "reason": self.reason})
        return json.dumps({
            "tool": self.tool,
            "params": self.params,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "source": self.source,
        })


# Rule-based intent → tool mapping (ordered most-specific first).
_INTENT_TOOL_RULES: list[tuple[re.Pattern[str], str, dict[str, Any], float, str]] = [
    (
        re.compile(r"\b(send|message|text)\b.*\bwhatsapp\b", re.I),
        "send_whatsapp",
        {},
        0.88,
        "whatsapp_send",
    ),
    (
        re.compile(
            r"\b(search\b.*\band\s+scroll|search\s+and\s+(open|browse|read|scroll)|browse.*\bscroll)\b",
            re.I,
        ),
        "search_and_browse",
        {"scroll": True},
        0.86,
        "compound_search",
    ),
    (
        re.compile(r"\b(read|get|show)\s+(the\s+)?(news|headlines)\b", re.I),
        "read_headlines",
        {"query": "latest news"},
        0.84,
        "news_headlines",
    ),
    (
        re.compile(r"\b(what is|who is|explain|smart search|tell me about)\b", re.I),
        "smart_search",
        {},
        0.80,
        "factual_qa",
    ),
    (
        re.compile(r"\b(search|google|look up|find)\b", re.I),
        "search_browser",
        {},
        0.78,
        "web_search",
    ),
    (
        re.compile(r"\b(play|start)\b.*\b(youtube\s+music|yt\s+music)\b", re.I),
        "play_youtube_music",
        {},
        0.84,
        "yt_music",
    ),
    (
        re.compile(r"\b(play|start)\b.*\bspotify\b", re.I),
        "play_spotify_music",
        {},
        0.84,
        "spotify_play",
    ),
    (
        re.compile(r"\b(play|start)\b.*\byoutube\b", re.I),
        "play_youtube",
        {},
        0.82,
        "youtube_play",
    ),
    (
        re.compile(r"\b(play|start)\b.*\b(music|song|track)\b", re.I),
        "play_music",
        {"platform": "spotify"},
        0.82,
        "play_music",
    ),
    (
        re.compile(r"\b(screenshot|screen\s*capture|capture\s+screen|take\s+a\s+snap)\b", re.I),
        "screenshot",
        {},
        0.90,
        "screenshot",
    ),
    (
        re.compile(r"\b(scroll)\s*(up|down)?\b", re.I),
        "mouse_scroll",
        {"amount": 3, "direction": "down"},
        0.86,
        "scroll",
    ),
    (
        re.compile(r"\b(speak|say|tell\s+me)\s+(something|a\s+joke|a\s+story|out\s+loud|aloud)\b", re.I),
        "none",
        {},
        0.92,
        "speak_aloud",
    ),
    (
        re.compile(r"\b(type|write|enter)\s+", re.I),
        "type_text",
        {},
        0.82,
        "type_text",
    ),
    (
        re.compile(r"\bclick\s+(?:at\s+)?(\d{2,4})\s*[, ]\s*(\d{2,4})\b", re.I),
        "click_at",
        {},
        0.90,
        "click_coords",
    ),
    (
        re.compile(r"\b(move|place)\s+(?:the\s+)?(mouse|cursor).*\b(\d{2,4})\s*[, ]\s*(\d{2,4})\b", re.I),
        "move_mouse",
        {},
        0.88,
        "move_coords",
    ),
    (
        re.compile(r"\b(press|hit)\s+((?:ctrl|alt|shift|win).+)\b", re.I),
        "hotkey",
        {},
        0.84,
        "hotkey",
    ),
    (
        re.compile(r"\b(click|press|tap)\b.*(button|link|icon|login|submit)\b", re.I),
        "browser_agent",
        {},
        0.76,
        "ui_click",
    ),
    (
        re.compile(r"\b(open|launch|start|run)\s+", re.I),
        "open_app",
        {},
        0.80,
        "open_app",
    ),
    (
        re.compile(r"\b(mute|unmute|volume|louder|quieter)\b", re.I),
        "volume_control",
        {},
        0.84,
        "volume",
    ),
    (
        re.compile(r"\b(task\s*manager|system\s*info|ipconfig|battery|hostname)\b", re.I),
        "system_command",
        {},
        0.82,
        "system_cmd",
    ),
    (
        re.compile(r"\b(stop|cancel)\s+(task|tasks|background|everything)\b", re.I),
        "cancel_task",
        {"task_type": "all"},
        0.88,
        "cancel_task",
    ),
    (
        re.compile(r"^(hi|hello|hey|thanks|thank you)\b", re.I),
        "chat",
        {},
        0.92,
        "greeting",
    ),
]


class GroqDecisionEngine:
    """
    Tool-selection engine: rules first, Groq LLM as fallback.

    Tool catalog is sourced from ``ToolRegistry`` merged with
    ``skills_registry.json`` guidance fields.
    """

    def __init__(self) -> None:
        self._allowed_tools: frozenset[str] = frozenset()
        self._refresh_tool_allowlist()

    def _refresh_tool_allowlist(self) -> None:
        catalog = self._build_tool_catalog()
        self._allowed_tools = frozenset(t["name"] for t in catalog)

    def _build_tool_catalog(self) -> list[dict[str, Any]]:
        """Merge ToolRegistry MCP schemas with skills_registry LLM guidance."""
        registry = get_tool_registry()
        merged: dict[str, dict[str, Any]] = {
            t["name"]: dict(t) for t in registry.get_tools_for_llm(include_sync=True)
        }
        for entry in load_skills_registry():
            name = entry.get("name")
            if not name:
                continue
            base = merged.setdefault(name, {"name": name, "description": entry.get("description", "")})
            for key in (
                "when_to_use",
                "when_not_to_use",
                "examples",
                "triggers",
                "safety_level",
                "requires_confirmation",
                "timeout_seconds",
                "retry_count",
            ):
                if entry.get(key) is not None:
                    base[key] = entry[key]
        return list(merged.values())

    def decide(self, screen_description: str, user_intent: str | None = None) -> str:
        """Return a JSON string (backward-compatible with vision_agent_loop)."""
        logger.info("Tool decision for intent: %r", user_intent)

        rule_decision = self._try_rule_decision(user_intent, screen_description)
        if rule_decision and rule_decision.confidence >= RULE_SKIP_LLM_THRESHOLD:
            logger.info(
                "Rule fast-path: %s (%.2f, %s)",
                rule_decision.tool,
                rule_decision.confidence,
                rule_decision.reason,
            )
            return rule_decision.to_json()

        if not client:
            logger.warning("Groq client not configured — using rule fallback")
            fallback = rule_decision or self._fallback_decision(user_intent, screen_description)
            return fallback.to_json()

        prompt = self._build_prompt(screen_description, user_intent, rule_decision)
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": build_tool_system_prompt(),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=360,
            )
            content = (response.choices[0].message.content or "").strip()
            logger.debug("Groq raw response: %s", content[:320])

            parsed = _parse_json_response(content)
            if not parsed:
                logger.warning("Unparseable Groq response — rule fallback")
                fallback = rule_decision or self._fallback_decision(user_intent, screen_description)
                return fallback.to_json()

            validated = self._validate_decision(parsed)
            if validated.action == "none":
                return validated.to_json()

            tool = validated.tool or "browser_agent"
            params = _enrich_params(tool, validated.params, user_intent)
            confidence = validated.confidence

            if tool == "type_text" and not params.get("text"):
                confidence = min(confidence, 0.50)
            if tool in ("search_browser", "search_and_browse", "smart_search") and not params.get("query"):
                confidence = min(confidence, 0.50)

            if confidence < 0.50:
                fallback = rule_decision or self._fallback_decision(user_intent, screen_description)
                if fallback.confidence > confidence:
                    return fallback.to_json()

            return ToolDecision(
                tool=tool,
                params=params,
                confidence=confidence,
                reason=validated.reason,
                source="groq",
            ).to_json()

        except Exception as exc:
            logger.error("Groq decision failed: %s", exc)
            fallback = rule_decision or self._fallback_decision(user_intent, screen_description)
            return fallback.to_json()

    def _try_rule_decision(
        self,
        user_intent: str | None,
        screen_description: str,
    ) -> ToolDecision | None:
        text = (user_intent or screen_description or "").strip()
        if not text:
            return ToolDecision(action="none", confidence=0.6, reason="empty_intent", source="rule")

        if re.search(r"\bsearch\b", text, re.I) and re.search(r"\bscroll\b", text, re.I):
            params = _enrich_params("search_and_browse", {"scroll": True}, user_intent)
            return ToolDecision(
                tool="search_and_browse",
                params=params,
                confidence=0.88,
                reason="compound_search_scroll",
                source="rule",
            )

        best: ToolDecision | None = None
        for pattern, tool, defaults, conf, rule_name in _INTENT_TOOL_RULES:
            if not pattern.search(text):
                continue
            if best is None or conf > best.confidence:
                params = _enrich_params(tool, dict(defaults), user_intent)
                if tool == "click_at":
                    m = re.search(r"\bclick\s+(?:at\s+)?(\d{2,4})\s*[, ]\s*(\d{2,4})\b", text, re.I)
                    if m:
                        params["x"], params["y"] = int(m.group(1)), int(m.group(2))
                if tool == "move_mouse":
                    m = re.search(r"\b(\d{2,4})\s*[, ]\s*(\d{2,4})\b", text)
                    if m:
                        params["x"], params["y"] = int(m.group(1)), int(m.group(2))
                if tool == "hotkey":
                    m = re.search(r"\b(?:press|hit)\s+(.+?)\s*$", text, re.I)
                    if m:
                        params["keys"] = m.group(1).strip()
                best = ToolDecision(
                    tool=tool,
                    params=params,
                    confidence=conf,
                    reason=rule_name,
                    source="rule",
                )
        return best

    def _fallback_decision(self, user_intent: str | None, screen_description: str) -> ToolDecision:
        rule = self._try_rule_decision(user_intent, screen_description)
        if rule and rule.tool:
            logger.info("Fallback decision: %s (%.2f)", rule.tool, rule.confidence)
            return rule
        text = (user_intent or screen_description or "Complete the user request").strip()
        return ToolDecision(
            tool="browser_agent",
            params={"task": text},
            confidence=0.55,
            reason="default_browser_agent",
            source="fallback",
        )

    def _validate_decision(self, decision: dict[str, Any]) -> ToolDecision:
        if decision.get("action") == "none":
            return ToolDecision(
                action="none",
                confidence=float(decision.get("confidence", 0.9)),
                reason=str(decision.get("reason", "")),
                source="groq",
            )

        tool = str(decision.get("tool", "")).strip()
        if tool not in self._allowed_tools:
            logger.warning("Disallowed tool %r — falling back to smart_search", tool)
            return ToolDecision(
                tool="smart_search",
                params={"query": _rewrite_vague_query(user_intent_hint(decision), None)},
                confidence=0.45,
                reason="invalid_tool_fallback",
                source="groq",
            )

        params = decision.get("params", {})
        if not isinstance(params, dict):
            params = {}

        confidence = float(decision.get("confidence", 0.75))
        return ToolDecision(
            tool=tool,
            params=params,
            confidence=max(0.0, min(0.99, confidence)),
            reason=str(decision.get("reason", "")),
            source="groq",
        )

    def _build_prompt(
        self,
        screen_description: str,
        user_intent: str | None,
        rule_hint: ToolDecision | None,
    ) -> str:
        tools_block = _format_tool_catalog(self._build_tool_catalog())
        intent_line = user_intent or "Observe the screen and determine if any action is required."
        hint_line = ""
        if rule_hint and rule_hint.tool:
            hint_line = (
                f"\nRULE GUESS: tool={rule_hint.tool} confidence={rule_hint.confidence:.2f} "
                f"params={rule_hint.params} reason={rule_hint.reason}\n"
                "Prefer the rule guess unless the screen context clearly requires a different tool.\n"
            )

        return f"""Select the NEXT tool for F.R.I.D.A.Y. to execute on Windows.

SCREEN (truncated):
{screen_description[:1200]}

USER INTENT:
{intent_line}
{hint_line}
DECISION TREE (apply in order)
1. WhatsApp message → send_whatsapp (name + message). Never open_app first.
2. Search + open/read/scroll article → search_and_browse (NOT search_browser).
3. Search only, no navigation → search_browser.
4. Factual Q&A ("what is", "who is") → smart_search.
5. News headlines → read_headlines.
6. Browser UI / search / music / multi-step web workflow → browser_agent (params.task = full description).
7. Native desktop app only (WhatsApp, Notepad) → os_control with params.task.
8. Explicit pixel coordinates → click_at or move_mouse (never browser_agent).
9. Simple scroll only → mouse_scroll.
10. Exact text to type at cursor → type_text (NOT for "speak/say/tell me" — those are conversational).
11. Keyboard shortcut → hotkey.

PARAMETER RULES
- NEVER pass vague queries: "any article", "something", "anything" → rewrite to a SPECIFIC topic.
- play_music default: song "AC/DC Back in Black", platform "spotify".
- search_and_browse: always include concrete params.query.
- browser_agent: params.task must be the full natural-language instruction (DOM-based, no vision).

OUTPUT (JSON only, no markdown)
Invoke: {{"tool": "name", "params": {{}}, "confidence": 0.0-1.0, "reason": "brief"}}
No action: {{"action": "none", "confidence": 0.9, "reason": "brief"}}

CONFIDENCE
0.90+ explicit command with concrete params
0.75–0.89 clear intent, params inferred
0.55–0.74 uncertain — prefer browser_agent for web UI; else action:none
<0.55 → action:none unless screen demands action

AVAILABLE TOOLS (from ToolRegistry v3)
{tools_block}
"""


_engine = GroqDecisionEngine()


def get_available_tools() -> list[dict[str, Any]]:
    """Return the merged tool catalog (for prompts and debugging)."""
    return _engine._build_tool_catalog()


def decide_action(screen_description: str, user_intent: str | None = None) -> str:
    """Backward-compatible entry point used by vision_agent_loop."""
    return _engine.decide(screen_description, user_intent)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def user_intent_hint(decision: dict[str, Any]) -> str:
    return str(decision.get("reason") or decision.get("tool") or "user request")


def _is_vague(value: str) -> bool:
    lower = value.lower().strip()
    if not lower or lower in _VAGUE_TERMS:
        return True
    return any(term in lower for term in _VAGUE_TERMS)


def _rewrite_vague_query(query: str, user_intent: str | None = None) -> str:
    if not _is_vague(query):
        return query.strip()
    seed = _REWRITE_TOPICS[hash((user_intent or query)[:80]) % len(_REWRITE_TOPICS)]
    logger.info("Rewrote vague query %r → %r", query, seed)
    return seed


def _extract_query_from_intent(user_intent: str) -> str:
    q = re.sub(
        r".*\b(search|look up|find|google|browse|read)\s+(for\s+|about\s+)?",
        "",
        user_intent,
        count=1,
        flags=re.I,
    ).strip()
    q = q.rstrip("?!. ")
    return q if q and not _is_vague(q) else _rewrite_vague_query(q or "news", user_intent)


def _extract_type_text(user_intent: str) -> str:
    text = re.sub(r"^(type|write|enter)\s+", "", user_intent, flags=re.I).strip()
    return text.strip("\"'")


def _extract_app_name(user_intent: str) -> str:
    app = re.sub(r"^(open|launch|start|run)\s+", "", user_intent, flags=re.I).strip()
    return app.split()[0] if app else "notepad"


def _enrich_params(tool: str, params: dict[str, Any], user_intent: str | None) -> dict[str, Any]:
    params = dict(params or {})
    intent_lower = (user_intent or "").lower()

    if tool in ("search_browser", "search_and_browse", "smart_search", "read_headlines"):
        query = params.get("query") or params.get("topic") or ""
        if not query and user_intent:
            query = _extract_query_from_intent(user_intent)
        params["query"] = _rewrite_vague_query(str(query), user_intent)
        if tool == "read_headlines" and not params["query"]:
            params["query"] = "latest news"

    if tool == "type_text" and not params.get("text") and user_intent:
        params["text"] = _extract_type_text(user_intent)

    if tool == "open_app" and not params.get("app") and user_intent:
        params["app"] = _extract_app_name(user_intent)

    if tool in ("play_music", "play_spotify_music", "play_youtube", "play_youtube_music"):
        if not params.get("song") and user_intent:
            from executor.music_player import parse_music_command

            parsed = parse_music_command(user_intent)
            params.update({k: v for k, v in parsed.items() if v})
        if tool == "play_music" and not params.get("platform"):
            params["platform"] = "spotify"
        if not params.get("song"):
            params["song"] = "AC/DC Back in Black"

    if tool in ("web_agent", "browser_agent") and not params.get("task"):
        params["task"] = user_intent or "Complete the user request on screen"

    if tool == "mouse_scroll":
        params.setdefault("amount", 3)
        params["direction"] = "up" if "up" in intent_lower and "down" not in intent_lower else params.get("direction", "down")

    if tool == "volume_control" and not params.get("action"):
        if "unmute" in intent_lower:
            params["action"] = "unmute"
        elif "up" in intent_lower or "louder" in intent_lower:
            params["action"] = "up"
        elif "down" in intent_lower or "quieter" in intent_lower:
            params["action"] = "down"
        else:
            params["action"] = "mute"

    if tool == "system_command" and not params.get("command"):
        if "task" in intent_lower and "manager" in intent_lower:
            params["command"] = "taskmgr"
        elif "ip" in intent_lower:
            params["command"] = "ipconfig"
        elif "battery" in intent_lower:
            params["command"] = "powercfg /batteryreport"
        else:
            params["command"] = "systeminfo"

    if tool == "send_whatsapp" and user_intent:
        from brain.router import _param_whatsapp

        wa_params = _param_whatsapp(None, user_intent)
        if wa_params.get("contact") and not params.get("name"):
            params["name"] = wa_params["contact"]
        if wa_params.get("message") and not params.get("message"):
            params["message"] = wa_params["message"]

    if tool == "hotkey" and not params.get("keys") and user_intent:
        from brain.router import _param_hotkey

        hk = _param_hotkey(None, user_intent)
        if hk.get("keys"):
            params["keys"] = "+".join(hk["keys"])

    return params


def _format_tool_catalog(tools: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for tool in tools:
        name = tool.get("name", "")
        desc = tool.get("description", "")
        safety = tool.get("safety_level", "low")
        confirm = tool.get("requires_confirmation", False)
        when = tool.get("when_to_use", "")
        not_when = tool.get("when_not_to_use", "")
        props = tool.get("parameters", {}).get("properties", {})
        param_names = ", ".join(props.keys()) if props else "none"
        lines.append(f'- "{name}" [{safety}{", confirm" if confirm else ""}]: {desc}')
        if when:
            lines.append(f"    USE WHEN: {when}")
        if not_when:
            lines.append(f"    AVOID WHEN: {not_when}")
        lines.append(f"    PARAMS: {{{param_names}}}")
        examples = tool.get("examples") or []
        if examples:
            ex = examples[0].get("tool_call", {})
            lines.append(f"    EXAMPLE: {json.dumps(ex)}")
    return "\n".join(lines)


def _parse_json_response(content: str) -> dict[str, Any] | None:
    """Multi-strategy JSON extraction from LLM output."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = cleaned.split("```")[0].strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL)
    if match:
        fragment = match.group()
        fragment = re.sub(r",\s*}", "}", fragment)
        fragment = re.sub(r",\s*]", "]", fragment)
        try:
            data = json.loads(fragment)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass

    tool_match = re.search(r'"tool"\s*:\s*"([a-z_]+)"', cleaned)
    if tool_match:
        return {"tool": tool_match.group(1), "params": {}, "confidence": 0.55, "reason": "loose_parse"}
    return None