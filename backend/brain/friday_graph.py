"""
friday_graph.py — FRIDAY Brain Pipeline
========================================
LangGraph state machine for end-to-end command processing.

Node map (logical roles)
------------------------
1. perceive  — Normalize input, gather OS context, hydrate memory
2. classify  — Rule-based fast-path first; LLM only when rules are uncertain
3. clarify   — Ask the user when intent confidence is too low
4. confirm   — Gate destructive/sensitive actions behind explicit confirmation
5. plan      — Build an execution plan (deterministic or LLM-assisted)
6. execute   — Run one plan step per visit
7. reflect   — Decide: continue plan, retry failed step, or finish
8. respond   — Synthesize the user-facing reply (+ TTS text for the speak layer)
9. remember  — Persist the exchange to short/long-term memory (persist layer)

Flow::

    perceive → classify ─┬→ clarify → END
                         ├→ confirm → END
                         ├→ respond (conversational fast-path)
                         ├→ execute (deterministic fast-path)
                         └→ plan → execute ↔ reflect → respond → remember → END
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional

from langgraph.graph import END, StateGraph

from brain.memory_manager import MemoryManager
from brain.groq_client import groq_complete
from brain.command_enhancer import enhance_command
from brain.router import ClassificationResult, IntentRouter
from brain.response_builder import get_builder
from brain.state import AgentState, ExecutionStatus, IntentCategory, ToolCall
from executor.tools_registry import ToolRegistry

logger = logging.getLogger("friday.brain")

# ---------------------------------------------------------------------------
# Shared services (process-wide singletons)
# ---------------------------------------------------------------------------

_router = IntentRouter()
_tools = ToolRegistry()
_memory = MemoryManager()

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 6
MAX_RETRIES = 2

# Rule confidence at or above this skips the LLM classifier entirely.
FAST_PATH_CONFIDENCE = 0.88

# Below this the router asks the user to clarify (unless intent is CHAT/UNKNOWN).
CLARIFY_THRESHOLD = 0.55

MEMORY_RETRIEVE_TIMEOUT_S = 1.5
MEMORY_MIN_QUERY_LEN = 18
TTS_MAX_CHARS = 280

WAKE_WORD_PREFIXES = ("hey friday", "friday", "okay friday", "ok friday")
SCREEN_CONTEXT_RE = re.compile(
    r"\b(what'?s on|what is on|on (?:my |the )?(?:screen|window|display)|"
    r"this (?:code|file|window|screen|page)|read the screen)\b",
    re.I,
)
_SKIP_MEMORY_RE = re.compile(
    r"\b(what time|what'?s the time|current time|joke|hello|good (?:morning|afternoon|evening)|"
    r"introduce yourself|open |launch |volume |mute|unmute|play )\b",
    re.I,
)
GREETING_PATTERN = re.compile(r"^(hi|hello|hey|thanks|ok|okay)\b", re.IGNORECASE)
INTRO_REQUEST_PATTERN = re.compile(
    r"\b(?:introduce\s+(?:yourself|your\s+self)|who\s+are\s+you|what\s+are\s+you|"
    r"tell\s+me\s+about\s+(?:yourself|you)|what\s+do\s+you\s+do)\b",
    re.IGNORECASE,
)

CONVERSATIONAL_INTENTS: frozenset[IntentCategory] = frozenset({
    IntentCategory.CHAT,
    IntentCategory.EXPLAIN,
    IntentCategory.CODE_HELP,
    IntentCategory.WRITE_TEXT,
    IntentCategory.SUMMARISE,
    IntentCategory.TRANSLATE,
    IntentCategory.PRESENCE_MODE,   # handled inline in node_respond — no tools needed
    IntentCategory.STOP,            # handled inline — cancels everything immediately
    IntentCategory.STANDING_ORDER,  # handled inline — writes to standing_orders store
})

# Intents that always require user confirmation before execution.
CONFIRMATION_INTENTS: frozenset[IntentCategory] = frozenset({
    IntentCategory.SHUTDOWN,
    IntentCategory.RESTART,
    IntentCategory.SLEEP,
    IntentCategory.LOCK,
})

CONFIRMATION_PROMPTS: dict[IntentCategory, str] = {
    IntentCategory.SHUTDOWN: "Shut down the computer in 30 seconds? Say yes to confirm.",
    IntentCategory.RESTART: "Restart the computer in 30 seconds? Say yes to confirm.",
    IntentCategory.SLEEP: "Put the computer to sleep? Say yes to confirm.",
    IntentCategory.LOCK: "Lock the screen now? Say yes to confirm.",
}


class ReflectDecision(str, Enum):
    """Outcome of the reflect node — drives graph routing."""

    CONTINUE = "continue"  # more plan steps remain
    RETRY = "retry"        # re-run the last failed step
    FINISH = "finish"      # proceed to respond


class GraphRoute(str, Enum):
    """Named routes used by conditional edges."""

    CLARIFY = "clarify"
    CONFIRM = "confirm"
    PLAN = "plan"
    EXECUTE = "execute"
    REFLECT = "reflect"
    RESPOND = "respond"


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


async def _call_llm(
    state: AgentState,
    prompt: str,
    *,
    max_tokens: int = 600,
    stream: bool = False,
) -> str:
    """Route a completion request to the configured LLM provider."""
    from config import settings, use_ollama
    from services.runtime_state import flags

    if flags.voice_turn or use_ollama():
        max_tokens = min(max_tokens, 180)

    model = state.get("llm_model") or settings.LLM_MODEL
    return await groq_complete(prompt, model=model, max_tokens=max_tokens, stream=stream)


# ---------------------------------------------------------------------------
# Perceive helpers — input normalization & memory hydration
# ---------------------------------------------------------------------------


def _strip_wake_words(text: str) -> str:
    """Remove leading wake-word prefixes from normalized input."""
    cleaned = text.lower()
    for prefix in WAKE_WORD_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned


_STT_PHRASE_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^i['']?m\s+your\s+joke\.?$", re.I), "tell me a joke"),
    (re.compile(r"^your\s+joke\.?$", re.I), "tell me a joke"),
    (re.compile(r"^let['']?s\s+ask\s+you\s+", re.I), ""),
    (re.compile(r"^can\s+you\s+", re.I), ""),
]


def _normalize_stt_phrasing(text: str) -> str:
    """Fix common misheard voice phrases before routing."""
    cleaned = (text or "").strip()
    for pattern, replacement in _STT_PHRASE_FIXES:
        if pattern.search(cleaned):
            cleaned = pattern.sub(replacement, cleaned).strip()
    return cleaned or text


def _should_retrieve_memories(cleaned_input: str) -> bool:
    """Skip vector retrieval for greetings, commands, and very short utterances."""
    if len(cleaned_input) < MEMORY_MIN_QUERY_LEN:
        return False
    if GREETING_PATTERN.match(cleaned_input) or _SKIP_MEMORY_RE.search(cleaned_input):
        return False
    return True


async def _retrieve_memories(query: str) -> list[str]:
    """Bounded long-term memory lookup — never blocks the hot path indefinitely."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_memory.retrieve, query, 3),
            timeout=MEMORY_RETRIEVE_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("Memory retrieval skipped: %s", exc)
        return []


def _summarize_short_term(short_term: list[dict[str, Any]], limit: int = 6) -> str:
    """Compact recent dialogue for classifier / responder context."""
    if not short_term:
        return ""
    lines: list[str] = []
    for msg in short_term[-limit:]:
        role = msg.get("role", "unknown").upper()
        content = str(msg.get("content", ""))[:120]
        intent = msg.get("intent")
        suffix = f" [{intent}]" if intent else ""
        lines.append(f"{role}{suffix}: {content}")
    return "\n".join(lines)


def _build_memory_context(
    short_term: list[dict[str, Any]],
    retrieved: list[str],
    prefs: dict[str, Any],
) -> str:
    """
    Fuse short-term dialogue, episodic retrieval, and user prefs into one block
    consumed by classify / respond nodes.
    """
    sections: list[str] = []

    if prefs:
        name = prefs.get("name", "User")
        tone = prefs.get("tone", "concise, friendly")
        sections.append(f"User prefs: name={name}, tone={tone}")

    history = _summarize_short_term(short_term)
    if history:
        sections.append(f"Recent dialogue:\n{history}")

    if retrieved:
        snippets = "\n".join(f"- {doc[:200]}" for doc in retrieved[:3])
        sections.append(f"Relevant memories:\n{snippets}")

    return "\n\n".join(sections)


async def _maybe_capture_screen(cleaned_input: str) -> Optional[str]:
    """OCR the active window when the user refers to on-screen content."""
    if not SCREEN_CONTEXT_RE.search(cleaned_input or ""):
        return None
    from executor.mouse_controller import ComputerController

    ctrl = ComputerController()
    return await ctrl.capture_screen_text(region="active_window")


# ---------------------------------------------------------------------------
# Classify helpers — rule fast-path + confirmation gating
# ---------------------------------------------------------------------------


async def _classify_intent(state: AgentState) -> dict[str, Any]:
    """
    Hybrid classification with a strong rule fast-path.

    1. Run synchronous regex rules (``router._rule_classify``).
    2. If confidence ≥ FAST_PATH_CONFIDENCE → return immediately (no LLM).
    3. Otherwise fall back to full ``router.classify`` (rules + LLM).
    """
    text = state["cleaned_input"]
    if _looks_like_fragment(text):
        return {
            "intent": IntentCategory.CHAT,
            "confidence": 0.9,
            "params": {},
            "source": "fragment",
            "fast_path": True,
        }
    rule_result: ClassificationResult = _router._rule_classify(text)

    if rule_result.confidence >= FAST_PATH_CONFIDENCE:
        logger.debug(
            "Fast-path hit: %s (%.2f via %s)",
            rule_result.intent.value,
            rule_result.confidence,
            rule_result.source,
        )
        return {
            "intent": rule_result.intent,
            "confidence": rule_result.confidence,
            "params": rule_result.params,
            "source": "rule",
            "fast_path": True,
        }

    # Rules were inconclusive — allow LLM disambiguation.
    hybrid = await _router.classify(
        text=text,
        context=state.get("short_term"),
        screen_context=state.get("screen_context"),
        active_window=state.get("active_window"),
    )
    intent = hybrid["intent"]
    if intent in (IntentCategory.SEARCH_WEB, IntentCategory.EXPLAIN) and (
        _looks_like_fragment(text) or not _looks_like_knowledge_query(text)
    ):
        intent = IntentCategory.CHAT
    return {
        "intent": intent,
        "confidence": hybrid["confidence"],
        "params": hybrid["params"],
        "source": hybrid.get("source", "llm"),
        "fast_path": hybrid.get("source") == "rule" and hybrid["confidence"] >= FAST_PATH_CONFIDENCE,
    }


def _needs_clarification(intent: IntentCategory, confidence: float) -> bool:
    """Low-confidence non-conversational intents need user clarification."""
    if confidence >= CLARIFY_THRESHOLD:
        return False
    return intent not in (IntentCategory.CHAT, IntentCategory.UNKNOWN)


def _needs_confirmation(intent: IntentCategory, params: dict[str, Any]) -> bool:
    """Check intent-level and tool-registry safety flags."""
    if intent in CONFIRMATION_INTENTS:
        return True

    simple_plan = _tools.get_simple_plan(intent, params)
    if not simple_plan:
        return False

    tool_name = simple_plan[0].split(":", 1)[0].strip()
    definition = _tools.get_definition(tool_name)
    return bool(definition and definition.requires_confirmation)


def _build_confirmation_prompt(intent: IntentCategory) -> str:
    return CONFIRMATION_PROMPTS.get(
        intent,
        "This action may have significant effects. Say yes to confirm.",
    )


_KNOWLEDGE_QUERY_RE = re.compile(
    r"\b(search|google|look up|find|what is|what are|what's|whats|"
    r"who is|who are|who's|tell me about|explain|define)\b",
    re.I,
)
_FRAGMENT_LEAD_RE = re.compile(
    r"^(for|to|and|or|but|with|from|about|of|the|all)\b",
    re.I,
)


def _looks_like_knowledge_query(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if cleaned.endswith("?"):
        return True
    return bool(_KNOWLEDGE_QUERY_RE.search(cleaned))


def _looks_like_fragment(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    words = cleaned.split()
    if cleaned.endswith("?") or _looks_like_knowledge_query(cleaned):
        return False
    if _FRAGMENT_LEAD_RE.match(cleaned) and len(words) <= 10:
        return True
    return False


def _maybe_prefill_plan(
    intent: IntentCategory,
    params: dict[str, Any],
    fast_path: bool,
    *,
    cleaned_input: str = "",
) -> tuple[list[str], int]:
    """
    Deterministic plan shortcut — skips the LLM planner when a direct tool
    mapping exists (typically on rule fast-path hits).
    """
    if intent == IntentCategory.EXPLAIN and _looks_like_knowledge_query(cleaned_input):
        query = (params.get("query") or cleaned_input or "").strip().rstrip("?.! ")
        if query:
            return [f"smart_search:{query}"], 0

    if not fast_path:
        return [], 0
    if intent == IntentCategory.OPEN_WHATSAPP:
        contact = (params.get("contact") or "").strip()
        return [f"send_whatsapp_message:{contact}"], 0
    simple = _tools.get_simple_plan(intent, params)
    if simple:
        return simple, 0
    return [], 0


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------


def _build_plan_prompt(state: AgentState) -> str:
    tools_list = "\n".join(f"- {t}" for t in _tools.list_tool_names())
    memory_block = state.get("memory_context") or ""
    return (
        "You are F.R.I.D.A.Y.'s planner. Decompose the user request into <=5 steps.\n"
        "IMPORTANT: Requests to speak, say, or tell the user something are CONVERSATIONAL — "
        "use chat: not keyboard_type or type_text.\n"
        f"Available tools:\n{tools_list}\n\n"
        f"User: {state['cleaned_input']}\n"
        f"Active window: {state.get('active_window') or 'unknown'}\n"
        f"{memory_block}\n"
        'Respond ONLY as a JSON list: ["tool_name:param", ...]\n'
        'Example: ["open_app:chrome", "keyboard_type:hello world"]'
    )


def _parse_plan_from_llm(text: str) -> list[str]:
    try:
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return [str(step) for step in parsed]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Reflect helpers — retry vs continue vs finish
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReflectOutcome:
    decision: ReflectDecision
    execution_status: ExecutionStatus
    next_route: GraphRoute
    max_retries: int
    current_step: int


def _decide_reflect(state: AgentState) -> ReflectOutcome:
    """
    Evaluate execution progress and choose the next transition.

    Priority:
    1. Continue — plan has remaining steps within iteration budget
    2. Retry    — last step failed and retries remain
    3. Finish   — all steps done (success, partial, or exhausted retries)
    """
    calls: list[ToolCall] = state.get("tool_calls") or []
    failed = [c for c in calls if c["status"] == ExecutionStatus.FAILED]
    step = state["current_step"]
    plan = state["plan"]
    iterations = state["iteration_count"]
    retries_left = state["max_retries"]

    # --- 1. More steps queued ---
    if step < len(plan) and iterations < MAX_ITERATIONS:
        return ReflectOutcome(
            ReflectDecision.CONTINUE,
            ExecutionStatus.PENDING,
            GraphRoute.EXECUTE,
            retries_left,
            step,
        )

    # --- 2. All steps attempted without failure ---
    if not failed:
        return ReflectOutcome(
            ReflectDecision.FINISH,
            ExecutionStatus.SUCCESS,
            GraphRoute.RESPOND,
            retries_left,
            step,
        )

    # --- 3. Failures with retry budget ---
    if retries_left > 0:
        retry_step = max(0, step - 1)
        logger.info(
            "Reflect: retrying step %d (%d retries left). Last error: %s",
            retry_step,
            retries_left,
            failed[-1].get("error"),
        )
        return ReflectOutcome(
            ReflectDecision.RETRY,
            ExecutionStatus.PENDING,
            GraphRoute.EXECUTE,
            retries_left - 1,
            retry_step,
        )

    # --- 4. Out of retries — partial or total failure ---
    if len(failed) < len(calls):
        status = ExecutionStatus.PARTIAL
    else:
        status = ExecutionStatus.FAILED

    return ReflectOutcome(
        ReflectDecision.FINISH,
        status,
        GraphRoute.RESPOND,
        retries_left,
        step,
    )


# ---------------------------------------------------------------------------
# Respond helpers
# ---------------------------------------------------------------------------


def _build_chat_prompt(state: AgentState) -> str:
    from brain.friday_persona import build_chat_system_prompt, is_factual_question

    intent = state.get("intent")
    intent_val = intent.value if hasattr(intent, "value") else str(intent or "")
    cleaned_input = state.get("cleaned_input") or ""
    history_limit = 2 if intent_val == "explain" or is_factual_question(cleaned_input) else 6
    history_text = _summarize_short_term(state.get("short_term") or [], limit=history_limit)
    memories = state.get("memory_context") or "\n".join(state.get("retrieved_memories") or [])
    prefs = state.get("user_preferences") or {}
    system = build_chat_system_prompt(
        memories=memories,
        history=history_text,
        user_name=prefs.get("name", "Boss"),
        active_window=state.get("active_window") or "",
        user_input=cleaned_input,
        intent=intent_val,
    )
    return (
        f"{system}\n\n"
        f"USER: {state['cleaned_input']}\n"
        "F.R.I.D.A.Y.:"
    )


def _spoken_now(state: AgentState | None = None) -> str:
    import datetime

    now = datetime.datetime.now()
    text = (state or {}).get("cleaned_input", "") if state else ""
    lower = str(text).lower()
    if "date" in lower or "day" in lower:
        return now.strftime("It's %A, %B %d, %Y, Boss.")
    return now.strftime("It's %I:%M %p on %A, %B %d.")


def _synthesise_response(
    intent: IntentCategory,
    status: ExecutionStatus,
    calls: list[ToolCall],
    state: AgentState,
) -> str:
    if status == ExecutionStatus.FAILED:
        err = str(calls[-1]["error"] if calls else "")
        lowered = err.lower()
        if "not found on path" in lowered or "couldn't find" in lowered or "not found" in lowered:
            return "I couldn't open that. Want me to try another way?"
        return "That didn't work. Want me to try a different approach?"

    if status == ExecutionStatus.PARTIAL:
        return "Partial success. One step failed — shall I retry it?"

    for call in reversed(calls):
        if call["status"] == ExecutionStatus.SUCCESS and call.get("result"):
            result = call["result"]
            if isinstance(result, dict) and result.get("message"):
                return str(result["message"])
            if isinstance(result, str):
                return result

    app = state["extracted_params"].get("app", "the app")
    if state["extracted_params"].get("fresh_workspace"):
        return "Opening VS Code with a fresh workspace now, Boss."

    confirmations: dict[IntentCategory, str] = {
        IntentCategory.MOUSE_CLICK: "Done, Boss. Click executed.",
        IntentCategory.KEYBOARD_TYPE: "Typed that in for you, Boss.",
        IntentCategory.OPEN_APP: f"Opening {app} now, Boss.",
        IntentCategory.VOLUME_SET: f"Volume at {state['extracted_params'].get('level', '?')} percent, Boss.",
        IntentCategory.SCREEN_CAPTURE: "Screenshot captured, Boss.",
        IntentCategory.TIME_DATE: _spoken_now(state),
        IntentCategory.PLAY_MEDIA: (
            f"Spinning up {state['extracted_params'].get('song', 'music')}, Boss."
            if state["extracted_params"].get("song")
            else "Resuming playback, Boss."
        ),
        IntentCategory.NEWS: "Here are the latest headlines, Boss.",
    }
    return confirmations.get(intent, "Done, Boss.")


def _clean_for_tts(text: str) -> str:
    from tts.pocket_tts import clean_text_for_speech
    from services.runtime_state import flags

    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`.*?`", "", text)
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"#+\s", "", text)
    if re.search(r"\[Groq (API key not configured|unavailable)", text, re.I):
        text = "My language service isn't available right now."
    text = re.sub(r"not found on PATH", "isn't installed", text, flags=re.I)
    text = clean_text_for_speech(text.strip())
    max_chars = 260 if flags.voice_turn else TTS_MAX_CHARS
    if len(text) > max_chars:
        # Split on full sentence endings rather than slicing mid-word
        sentences = re.split(r"(?<=[.!?])\s+", text)
        collected = []
        cur_len = 0
        for s in sentences:
            if cur_len + len(s) + 1 <= max_chars:
                collected.append(s)
                cur_len += len(s) + 1
            else:
                break
        if collected:
            text = " ".join(collected)
        else:
            # If a single sentence is long, split on clause boundaries
            clauses = re.split(r"(?<=[,;:])\s+", text)
            collected_c = []
            cur_c = 0
            for c in clauses:
                if cur_c + len(c) + 1 <= max_chars:
                    collected_c.append(c)
                    cur_c += len(c) + 1
                else:
                    break
            if collected_c:
                text = " ".join(collected_c).rstrip(",;:") + "."
    return text


def _build_ui_event(intent: IntentCategory, state: AgentState) -> Optional[dict[str, Any]]:
    card_map = {
        IntentCategory.WEATHER: "weather",
        IntentCategory.NEWS: "news",
        IntentCategory.SCREEN_CAPTURE: "screenshot",
        IntentCategory.SYSTEM_INFO: "stats",
    }
    card = card_map.get(intent)
    if not card:
        return None
    return {"card": card, "status": state.get("execution_status")}


# ---------------------------------------------------------------------------
# Graph nodes — each node mutates a small, focused slice of state
# ---------------------------------------------------------------------------


async def node_perceive(state: AgentState) -> AgentState:
    """
    Normalize input and hydrate contextual memory.

    Writes: cleaned_input, active_window, screen_context, short_term,
            retrieved_memories, user_preferences, memory_context
    Resets: iteration_count, tool_calls (new turn)
    """
    from executor.mouse_controller import ComputerController

    raw = state["raw_input"].strip()
    cleaned = _normalize_stt_phrasing(_strip_wake_words(raw))
    session_id = state["session_id"]

    ctrl = ComputerController()
    active_window, screen_context, short_term, prefs = await asyncio.gather(
        ctrl.get_active_window_title(),
        _maybe_capture_screen(cleaned),
        asyncio.to_thread(_memory.get_short_term, session_id),
        asyncio.to_thread(_memory.get_preferences, session_id),
    )
    retrieved = (
        await _retrieve_memories(cleaned)
        if _should_retrieve_memories(cleaned)
        else []
    )

    # Phase 5 — Standing orders context injection
    try:
        from brain.standing_orders import standing_orders as _so
        so_context = _so.to_context_string()
    except Exception:
        so_context = ""

    memory_context = _build_memory_context(short_term, retrieved, prefs)
    if so_context:
        memory_context = so_context + ("\n" + memory_context if memory_context else "")

    return {
        **state,
        "cleaned_input": cleaned or state["raw_input"].strip(),
        "active_window": active_window,
        "screen_context": screen_context,
        "short_term": short_term,
        "retrieved_memories": retrieved,
        "user_preferences": prefs,
        "memory_context": memory_context,
        "iteration_count": 0,
        "tool_calls": [],
        "route": "",
        "reflect_decision": "",
    }


async def node_enhance(state: AgentState) -> AgentState:
    """
    Pre-classify and enrich the user command before intent classification.

    Resolves WhatsApp contacts from the phonebook, infers platform defaults
    (e.g. music search → YouTube), and tags the command category.

    Writes: enhanced_input, enhanced_params, command_category, enhancer_hints
    """
    cleaned = state.get("cleaned_input") or state["raw_input"].strip()
    result = enhance_command(cleaned)

    return {
        **state,
        "enhanced_input": result.enhanced_input,
        "enhanced_params": result.enhanced_params,
        "command_category": result.category,
        "enhancer_hints": result.hints,
    }


async def node_classify(state: AgentState) -> AgentState:
    """
    Classify intent with rule fast-path, then set routing flags.

    Writes: intent, intent_confidence, extracted_params, classification_source,
            fast_path, needs_clarification, needs_confirmation, plan (optional)
    """
    classification = await _classify_intent(state)
    intent: IntentCategory = classification["intent"]
    confidence: float = classification["confidence"]
    params: dict[str, Any] = classification["params"]
    fast_path: bool = classification["fast_path"]

    needs_clarification = _needs_clarification(intent, confidence)
    needs_confirmation = (
        not needs_clarification
        and _needs_confirmation(intent, params)
    )

    clarification_prompt = (
        _router.build_clarification_prompt(intent, params) if needs_clarification else None
    )
    confirmation_prompt = (
        _build_confirmation_prompt(intent) if needs_confirmation else None
    )

    cleaned = state.get("cleaned_input") or state["raw_input"].strip()
    plan, current_step = _maybe_prefill_plan(
        intent,
        params,
        fast_path,
        cleaned_input=cleaned,
    )

    # Conversational intents skip tool execution unless a deterministic plan exists.
    route = ""
    if (
        not needs_clarification
        and not needs_confirmation
        and intent in CONVERSATIONAL_INTENTS
        and not plan
    ):
        route = GraphRoute.RESPOND.value

    # Merge enhanced_params from the enhancer into extracted_params.
    # Enhancer-resolved fields (contact, phone, platform, etc.) take precedence
    # when the classifier didn't extract them.
    enhanced = state.get("enhanced_params") or {}
    for key, val in enhanced.items():
        if val and not params.get(key):
            params[key] = val

    updates: AgentState = {
        **state,
        "intent": intent,
        "intent_confidence": confidence,
        "extracted_params": params,
        "classification_source": classification["source"],
        "fast_path": fast_path,
        "needs_clarification": needs_clarification,
        "clarification_prompt": clarification_prompt,
        "needs_confirmation": needs_confirmation,
        "confirmation_prompt": confirmation_prompt,
        "route": route,
    }

    if plan:
        updates["plan"] = plan
        updates["current_step"] = current_step

    from brain.model_router import resolve_llm_model

    updates["llm_model"] = resolve_llm_model(
        intent,
        params,
        cleaned_input=state.get("cleaned_input") or state.get("raw_input") or "",
    )

    return updates


async def node_clarify(state: AgentState) -> AgentState:
    """Return a clarification question without executing any tools."""
    prompt = state.get("clarification_prompt") or "Could you clarify what you'd like me to do?"
    return {
        **state,
        "final_response": prompt,
        "tts_text": _clean_for_tts(prompt),
        "execution_status": ExecutionStatus.PENDING,
        "route": GraphRoute.CLARIFY.value,
    }


async def node_confirm(state: AgentState) -> AgentState:
    """
    Hold sensitive actions until the user explicitly confirms.

    Execution is deferred — the next user turn should include affirmation
    (future: perceive can detect 'yes' and re-enter with confirmation granted).
    """
    prompt = state.get("confirmation_prompt") or "Please confirm before I proceed."
    return {
        **state,
        "final_response": prompt,
        "tts_text": _clean_for_tts(prompt),
        "execution_status": ExecutionStatus.PENDING,
        "route": GraphRoute.CONFIRM.value,
    }


async def node_plan(state: AgentState) -> AgentState:
    """
    Build an execution plan.

    Skips the LLM when a deterministic plan was pre-filled during classify.
    """
    if state.get("plan"):
        return state

    intent = state["intent"]
    params = state["extracted_params"]
    simple = _tools.get_simple_plan(intent, params)
    if simple:
        return {**state, "plan": simple, "current_step": 0}

    from brain.model_router import resolve_llm_model

    plan_model = resolve_llm_model(state["intent"], state.get("extracted_params"), for_plan=True)
    plan_state = {**state, "llm_model": plan_model}
    plan_prompt = _build_plan_prompt(state)
    llm_resp = await _call_llm(plan_state, plan_prompt, max_tokens=400)
    steps = _parse_plan_from_llm(llm_resp)
    if not steps:
        steps = [f"chat:{state['cleaned_input']}"]

    return {
        **state,
        "plan": steps,
        "current_step": 0,
        "llm_response": llm_resp,
        "llm_prompt": plan_prompt,
    }


async def node_execute(state: AgentState) -> AgentState:
    """Run exactly one plan step and append the result to tool_calls."""
    plan = state["plan"]
    step_idx = state["current_step"]

    if step_idx >= len(plan):
        return {**state, "route": GraphRoute.REFLECT.value}

    step = plan[step_idx]
    tool_name, _, raw_param = step.partition(":")

    tool_call_result = await _tools.execute(
        tool_name=tool_name.strip(),
        raw_param=raw_param.strip(),
        params=state["extracted_params"],
        state=state,
    )

    new_iteration = state["iteration_count"] + 1
    next_step = step_idx + 1
    next_route = (
        GraphRoute.REFLECT.value
        if (next_step >= len(plan) or new_iteration >= MAX_ITERATIONS)
        else GraphRoute.EXECUTE.value
    )

    existing_calls = list(state.get("tool_calls") or [])
    return {
        **state,
        "tool_calls": existing_calls + [tool_call_result],
        "current_step": next_step,
        "iteration_count": new_iteration,
        "route": next_route,
    }


async def node_reflect(state: AgentState) -> AgentState:
    """
    Evaluate execution progress and decide the next transition.

    Sets reflect_decision ∈ {continue, retry, finish} and execution_status.
    """
    outcome = _decide_reflect(state)
    return {
        **state,
        "reflect_decision": outcome.decision.value,
        "execution_status": outcome.execution_status,
        "max_retries": outcome.max_retries,
        "current_step": outcome.current_step,
        "route": outcome.next_route.value,
    }


async def node_respond(state: AgentState) -> AgentState:
    """Compose the final user-facing response and TTS payload."""
    intent = state["intent"]
    status = state.get("execution_status", ExecutionStatus.PENDING)
    calls = state.get("tool_calls") or []

    request = state.get("cleaned_input", "").lower().strip()
    if intent == IntentCategory.STOP:
        # Hard-stop everything immediately — then speak.
        try:
            from executor.stop import stop_all
            stop_all(reason="user_voice_command")
        except Exception as _se:
            logger.warning("stop_all failed: %s", _se)
        response = "Stopped."
        return {
            **state,
            "final_response": response,
            "tts_text": response,
            "ui_event": None,
            "llm_response": response,
        }

    if intent == IntentCategory.STANDING_ORDER:
        raw = state.get("cleaned_input") or state.get("raw_input") or ""
        try:
            from brain.standing_orders import standing_orders, StandingOrder, _parse_standing_order
            action, order = _parse_standing_order(raw)
            if action == "remove":
                standing_orders.remove_by_text(raw)
                response = "Standing order removed."
            elif order:
                standing_orders.add(order)
                response = f"Got it. I'll {order.instruction} from now on."
            else:
                response = "I'm not sure what standing order to set. Could you be more specific?"
        except Exception as _soe:
            logger.warning("Standing order handling failed: %s", _soe)
            response = "I couldn't update that standing order."
        return {
            **state,
            "final_response": response,
            "tts_text": _clean_for_tts(response),
            "ui_event": None,
            "llm_response": response,
        }

    if intent == IntentCategory.PRESENCE_MODE:
        # Apply the presence mode change and return a spoken confirmation.
        from services.presence import classify_presence_intent, PresenceMode
        text_for_presence = state.get("cleaned_input") or state.get("raw_input") or ""
        mode, duration_s = classify_presence_intent(text_for_presence)
        if mode is None:
            # Fallback: check raw input too
            mode, duration_s = classify_presence_intent(state.get("raw_input") or "")
        if mode == PresenceMode.SLEEP:
            if duration_s:
                mins = int(duration_s // 60)
                hrs = int(duration_s // 3600)
                if hrs >= 1:
                    time_str = f"{hrs} hour{'s' if hrs > 1 else ''}"
                else:
                    time_str = f"{mins} minute{'s' if mins > 1 else ''}"
                response = f"Going quiet for {time_str}. I'll be back."
            else:
                response = "Going to sleep. Say 'Hey Friday, I'm back' when you need me."
        elif mode == PresenceMode.QUIET:
            response = "Switching to watch mode. Wake-word only."
        elif mode == PresenceMode.RESIDENT:
            response = "I'm back."
        else:
            response = "Got it."
        # Apply mode asynchronously after responding so TTS fires first
        if mode is not None:
            async def _apply_later() -> None:
                import asyncio as _asyncio
                await _asyncio.sleep(0.8)  # let TTS start
                try:
                    from services.presence import presence
                    await presence.set_mode(mode, reason="user_command", duration_s=duration_s)
                except Exception as _e:
                    import logging
                    logging.getLogger("friday.presence").warning("Presence apply failed: %s", _e)
            asyncio.create_task(_apply_later())
        return {
            **state,
            "final_response": response,
            "tts_text": _clean_for_tts(response),
            "ui_event": None,
            "llm_response": response,
        }
    if intent == IntentCategory.CHAT and INTRO_REQUEST_PATTERN.search(request):
        response = get_builder().intro()
        return {
            **state,
            "final_response": response,
            "tts_text": response,
            "intro_audio": True,
            "ui_event": _build_ui_event(intent, state),
            "llm_response": response,
        }
    if intent == IntentCategory.CHAT:
        from brain.friday_persona import is_joke_request

        if is_joke_request(request):
            response = get_builder().joke()
        elif re.fullmatch(
            r"(?:hi|hello|hey|howdy|yo|good\s+(?:morning|afternoon|evening)|greetings)[!?. ]*",
            request,
        ):
            response = get_builder().greeting()
        elif _looks_like_fragment(request):
            response = "I only caught a fragment of that. What should I do with it?"
        else:
            response = await _call_llm(
                state,
                _build_chat_prompt(state),
                max_tokens=180,
                stream=True,
            )
    elif intent in CONVERSATIONAL_INTENTS:
        response = await _call_llm(
            state,
            _build_chat_prompt(state),
            max_tokens=180,
            stream=True,
        )
    else:
        response = _synthesise_response(intent, status, calls, state)

    return {
        **state,
        "final_response": response,
        "tts_text": _clean_for_tts(response),
        "ui_event": _build_ui_event(intent, state),
        "llm_response": response,
    }


async def node_remember(state: AgentState) -> AgentState:
    """Persist the completed exchange to session and long-term memory."""
    # Phase 5 — skip memory storage for sensitive turns
    raw_input = state["raw_input"]
    final_response = state.get("final_response", "")
    try:
        from brain.redact import contains_secret, safe_for_memory
        if contains_secret(raw_input):
            safe_input = safe_for_memory(raw_input) or "[REDACTED]"
        else:
            safe_input = raw_input
        safe_response = safe_for_memory(final_response) or "[REDACTED]"
    except Exception:
        safe_input = raw_input
        safe_response = final_response

    await asyncio.to_thread(
        _memory.add_exchange,
        state["session_id"],
        safe_input,
        safe_response,
        state["intent"].value,
        {
            "timestamp": state["timestamp"],
            "status": str(state.get("execution_status", "unknown")),
            "tools_used": [c["tool_name"] for c in state.get("tool_calls") or []],
            "classification_source": state.get("classification_source", ""),
            "fast_path": state.get("fast_path", False),
            "reflect_decision": state.get("reflect_decision", ""),
        },
    )
    return state


# ---------------------------------------------------------------------------
# Conditional routers
# ---------------------------------------------------------------------------


def route_after_classify(state: AgentState) -> str:
    """Route post-classification: clarify → confirm → respond → execute → plan."""
    if state.get("needs_clarification"):
        return GraphRoute.CLARIFY.value
    if state.get("needs_confirmation"):
        return GraphRoute.CONFIRM.value
    if state.get("route") == GraphRoute.RESPOND.value:
        return GraphRoute.RESPOND.value
    if state.get("plan") and state.get("fast_path"):
        return GraphRoute.EXECUTE.value
    return GraphRoute.PLAN.value


def route_after_execute(state: AgentState) -> str:
    """After each step: keep executing or hand off to reflect."""
    if (
        state["current_step"] < len(state["plan"])
        and state["iteration_count"] < MAX_ITERATIONS
        and state.get("route") != GraphRoute.REFLECT.value
    ):
        return GraphRoute.EXECUTE.value
    return GraphRoute.REFLECT.value


def route_after_reflect(state: AgentState) -> str:
    """Reflect outcome drives whether to retry/continue or respond."""
    route = state.get("route", GraphRoute.RESPOND.value)
    if route == GraphRoute.EXECUTE.value:
        return GraphRoute.EXECUTE.value
    return GraphRoute.RESPOND.value


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Wire the 8 logical nodes and conditional transitions."""
    graph = StateGraph(AgentState)

    graph.add_node("perceive", node_perceive)
    graph.add_node("enhance", node_enhance)
    graph.add_node("classify", node_classify)
    graph.add_node("clarify", node_clarify)
    graph.add_node("confirm", node_confirm)
    graph.add_node("plan", node_plan)
    graph.add_node("execute", node_execute)
    graph.add_node("reflect", node_reflect)
    graph.add_node("respond", node_respond)
    graph.add_node("remember", node_remember)

    graph.set_entry_point("perceive")
    graph.add_edge("perceive", "enhance")
    graph.add_edge("enhance", "classify")

    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            GraphRoute.CLARIFY.value: "clarify",
            GraphRoute.CONFIRM.value: "confirm",
            GraphRoute.RESPOND.value: "respond",
            GraphRoute.EXECUTE.value: "execute",
            GraphRoute.PLAN.value: "plan",
        },
    )

    graph.add_edge("plan", "execute")

    graph.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            GraphRoute.EXECUTE.value: "execute",
            GraphRoute.REFLECT.value: "reflect",
        },
    )

    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            GraphRoute.EXECUTE.value: "execute",
            GraphRoute.RESPOND.value: "respond",
        },
    )

    graph.add_edge("respond", "remember")
    graph.add_edge("remember", END)
    graph.add_edge("clarify", END)
    graph.add_edge("confirm", END)

    return graph.compile()


FRIDAY_GRAPH = build_graph()


def _initial_state(
    raw_input: str,
    session_id: str,
    llm_provider: str,
    llm_model: str,
) -> AgentState:
    """Factory for a clean turn-scoped AgentState."""
    return {
        "raw_input": raw_input,
        "session_id": session_id,
        "timestamp": time.time(),
        "cleaned_input": "",
        "screen_context": None,
        "active_window": None,
        "enhanced_input": None,
        "enhanced_params": {},
        "command_category": None,
        "enhancer_hints": [],
        "intent": IntentCategory.UNKNOWN,
        "intent_confidence": 0.0,
        "extracted_params": {},
        "needs_clarification": False,
        "clarification_prompt": None,
        "needs_confirmation": False,
        "confirmation_prompt": None,
        "plan": [],
        "tool_calls": [],
        "current_step": 0,
        "max_retries": MAX_RETRIES,
        "short_term": [],
        "retrieved_memories": [],
        "user_preferences": {},
        "memory_context": None,
        "classification_source": "",
        "fast_path": False,
        "reflect_decision": "",
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_prompt": None,
        "llm_response": None,
        "final_response": "",
        "tts_text": "",
        "ui_event": None,
        "execution_status": ExecutionStatus.PENDING,
        "route": "",
        "error_message": None,
        "iteration_count": 0,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_pipeline(
    raw_input: str,
    session_id: str = "default",
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> AgentState:
    """Execute the full graph for a single user turn."""
    from config import settings

    if llm_provider is None:
        llm_provider = settings.LLM_PROVIDER or "groq"
    if llm_model is None:
        llm_model = settings.LLM_MODEL

    initial_state = _initial_state(raw_input, session_id, llm_provider, llm_model)

    try:
        state = await FRIDAY_GRAPH.ainvoke(initial_state)
        # Refocus Friday window if any computer control tools were executed
        if state.get("tool_calls"):
            try:
                from executor.window_manager import bring_friday_to_front
                await asyncio.to_thread(bring_friday_to_front)
            except Exception as e:
                logger.error("Failed to refocus FRIDAY window: %s", e)
        return state
    except Exception as exc:
        logger.exception("Pipeline error")
        return {
            **initial_state,
            "final_response": f"Something went wrong: {exc}",
            "tts_text": "I ran into an error, sorry.",
            "execution_status": ExecutionStatus.FAILED,
            "error_message": str(exc),
        }


async def run_graph(
    user_input: str,
    thread_id: str = "default",
    history: Optional[list] = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    session_id: str = "default",
) -> dict[str, Any]:
    """Backward-compatible wrapper returning a summary dict for callers."""
    state = await run_pipeline(
        raw_input=user_input,
        session_id=session_id,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    intent = state["intent"]
    return {
        "response_text": state.get("final_response", ""),
        "intent": intent.value if hasattr(intent, "value") else str(intent),
        "skill": "",
        "messages": state.get("short_term", []),
        "execution_status": state.get("execution_status", ExecutionStatus.PENDING),
        "tool_calls": state.get("tool_calls", []),
        "raw_state": state,
    }


def get_graph():
    """Return the compiled LangGraph instance."""
    return FRIDAY_GRAPH


# Backward-compatible alias (evaluate → reflect rename)
node_evaluate = node_reflect
route_after_evaluate = route_after_reflect
