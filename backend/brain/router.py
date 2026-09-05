"""
router.py — FRIDAY Intent Router
=================================
Hybrid classification: rule-based fast path + LLM disambiguation + keyword fallback.

Pipeline (``IntentRouter.classify``)
------------------------------------
1. ``RuleEngine`` scores all matching patterns; highest confidence wins.
2. If confidence ≥ ``llm_skip_threshold`` → return rule result (no LLM).
3. Otherwise ask the LLM with intent hints + rule guess; merge with rules.
4. On LLM failure → keyword/heuristic fallback.

LangGraph integration (``friday_graph._classify_intent``)
---------------------------------------------------------
- Runs ``_rule_classify`` first; ≥0.88 fast-path skips ``classify`` entirely.
- Gray zone (0.82–0.87) calls ``classify``, which may invoke LLM disambiguation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from brain.state import IntentCategory

logger = logging.getLogger("friday.intent")

ParamExtractor = Callable[[re.Match[str] | None, str], dict[str, Any]]

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 0.99
CHAT_DEFAULT_CONFIDENCE = 0.62
UNKNOWN_FALLBACK_CONFIDENCE = 0.45

# Above this: return rule result without LLM (minimizes API calls).
DEFAULT_LLM_SKIP_THRESHOLD = 0.82

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|howdy|greetings|yo)\b",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(r"^(thanks|thank you|thx|cheers)\b", re.IGNORECASE)
_VAGUE_SEARCH_RE = re.compile(
    r"\b(any\s+article|something|anything|random\s+page|whatever|stuff)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Structured routing output consumed by LangGraph and legacy callers."""

    intent: IntentCategory
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "rule"
    matched_rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "params": self.params,
            "source": self.source,
            "matched_rule": self.matched_rule,
        }


# ---------------------------------------------------------------------------
# Parameter extractors
# ---------------------------------------------------------------------------


def _param_mouse_click(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    coord = re.search(r"\b(\d{2,4})\s*[, ]\s*(\d{2,4})\b", text)
    if coord:
        return {
            "x": int(coord.group(1)),
            "y": int(coord.group(2)),
            "button": "right" if re.search(r"\bright\b", text, re.I) else "left",
            "double": bool(re.search(r"\bdouble\b", text, re.I)),
        }
    loc = m.group("loc").strip() if m and m.groupdict().get("loc") else None
    return {
        "location": loc,
        "button": "right" if re.search(r"\bright\b", text, re.I) else "left",
        "double": bool(re.search(r"\bdouble\b", text, re.I)),
    }


def _param_mouse_move(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    coord = re.search(r"\b(\d{2,4})\s*[, ]\s*(\d{2,4})\b", text)
    if coord:
        return {"x": int(coord.group(1)), "y": int(coord.group(2))}
    x = m.group("x") if m and "x" in m.groupdict() and m.group("x") else None
    y = m.group("y") if m and "y" in m.groupdict() and m.group("y") else None
    loc = m.group("loc").strip() if m and m.groupdict().get("loc") else None
    return {
        "x": int(x) if x else None,
        "y": int(y) if y else None,
        "location": loc,
    }


def _param_keyboard_type(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    if m:
        trigger = m.group(0)
        typed = text[text.lower().find(trigger.lower()) + len(trigger):]
    else:
        typed = re.sub(r"^(type|write|enter|input)\s+", "", text, flags=re.I)
    return {"text": typed.strip().strip("\"'")}


def _param_open_app(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    if m and m.groupdict().get("app"):
        app = m.group("app")
    else:
        app = re.sub(r"^(open|launch|start|run)\s+", "", text, flags=re.I)
    app = re.sub(r"\s+(app|application|please|for me)\s*$", "", app.strip(), flags=re.I)
    app = app.strip("\"'`").strip()
    app = re.sub(r"[.!?,;:]+$", "", app).strip()
    return {"app": app}


def _param_new_project(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    """Open VS Code with a fresh workspace — not an existing project."""
    name = ""
    name_match = re.search(
        r"(?:called|named)\s+([\w][\w\-]*)",
        text,
        re.I,
    )
    if name_match:
        name = name_match.group(1)
    return {
        "app": "vscode",
        "fresh_workspace": True,
        "project_name": name,
    }


def _param_volume(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    reduce_by = re.search(
        r"\b(?:reduce|lower|decrease)\s+(?:the\s+)?volume\s+by\s+(\d{1,3})\b",
        text,
        re.I,
    )
    increase_by = re.search(
        r"\b(?:increase|raise)\s+(?:the\s+)?volume\s+by\s+(\d{1,3})\b",
        text,
        re.I,
    )
    if reduce_by:
        return {"level": int(reduce_by.group(1)), "direction": "reduce"}
    if increase_by:
        return {"level": int(increase_by.group(1)), "direction": "increase"}
    lvl_match = re.search(r"\b(\d{1,3})\s*(?:%|out\s+of\s+100)?\b", text)
    lvl = int(lvl_match.group(1)) if lvl_match else None
    if re.search(r"\b(mute|silence)\b", text, re.I):
        return {"level": 0, "direction": "set"}
    up = re.search(r"\b(up|increase|louder|raise)\b", text, re.I)
    dn = re.search(r"\b(down|decrease|quieter|lower|reduce)\b", text, re.I)
    return {"level": lvl, "direction": "up" if up else "down" if dn else "set"}


def _param_scroll(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    amt_match = re.search(r"\b(\d{1,2})\s*(times|steps|lines)?\b", text)
    amount = int(amt_match.group(1)) if amt_match else 3
    direction = "up" if re.search(r"\bup\b", text, re.I) else "down"
    return {"amount": amount, "direction": direction}


def _param_hotkey(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    after_press = re.search(r"\b(?:press|hit)\s+(.+?)\s*$", text, re.I)
    if after_press:
        chunk = after_press.group(1).strip()
        if "+" in chunk:
            keys = [k.strip().lower() for k in chunk.split("+") if k.strip()]
            keys = ["ctrl" if k == "control" else k for k in keys]
            if keys:
                return {"keys": keys}

    combo = re.search(
        r"\b((?:ctrl|control|alt|shift|win|cmd|super)(?:\s*\+\s*|\s+)){0,3}"
        r"(?:ctrl|control|alt|shift|win|cmd|super|tab|enter|escape|esc|delete|backspace|f\d+|\w+)\b",
        text,
        re.I,
    )
    if combo:
        parts = re.split(r"\s*\+\s*|\s+", combo.group(0).lower())
        keys = [p.strip() for p in parts if p.strip()]
        keys = ["ctrl" if k in ("control",) else k for k in keys]
        return {"keys": keys}

    action_map = {
        "copy": ["ctrl", "c"],
        "paste": ["ctrl", "v"],
        "undo": ["ctrl", "z"],
        "redo": ["ctrl", "y"],
        "select all": ["ctrl", "a"],
        "save": ["ctrl", "s"],
        "refresh": ["f5"],
        "reload": ["f5"],
        "new tab": ["ctrl", "t"],
        "close tab": ["ctrl", "w"],
        "switch tab": ["alt", "tab"],
        "alt tab": ["alt", "tab"],
        "fullscreen": ["f11"],
        "zoom in": ["ctrl", "+"],
        "zoom out": ["ctrl", "-"],
    }
    lower = text.lower()
    for phrase, keys in action_map.items():
        if phrase in lower:
            return {"keys": keys}
    return {"keys": []}


def _param_search(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    q = re.sub(
        r".*\b(search|look up|google|find|browse)\s+(for\s+|about\s+)?",
        "",
        text,
        count=1,
        flags=re.I,
    ).strip()
    q = re.sub(r"\s+(on\s+google|on\s+the\s+web|online)\s*$", "", q, flags=re.I)
    return {"query": q or text.strip()}


def _param_news(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    """Return a usable news topic instead of forwarding command wording."""
    topic = re.sub(
        r"^\s*(?:read|get|show|give me|what are)\s+(?:the\s+)?(?:latest\s+)?(?:news|headlines)\b",
        "",
        text,
        flags=re.I,
    )
    topic = re.sub(r"^\s*(?:about|on|for)\s+", "", topic, flags=re.I)
    topic = topic.strip(" ?!.,")
    return {"query": topic or "latest news"}


def _param_youtube(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    q = re.sub(r".*\b(play|search|open|watch|launch)\s+", "", text, flags=re.I).strip()
    q = re.sub(r"\s+(on|in)\s+youtube.*$", "", q, flags=re.I).strip()
    q = q.strip("\"'`").strip()
    q = re.sub(r"[.!?,;:]+$", "", q).strip()
    if re.fullmatch(r"(?:you\s*tube|youtube)", q, re.I):
        q = ""
    return {"query": q}


def _param_play_music(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    from executor.music_player import parse_music_command

    return parse_music_command(text)


def _param_whatsapp(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    # 1. Extract message content after "saying", "that", "say", "message"
    msg_m = re.search(r'(?:saying|say|that|message\s+as|with\s+text)\s+["\']?(.+?)["\']?\s*$', text, re.I)
    msg_text = msg_m.group(1).strip() if msg_m else None

    # 2. Extract contact name before "saying" / "on whatsapp" / "in whatsapp"
    contact_text = text[:msg_m.start()] if msg_m else text

    contact_m = re.search(
        r"(?:to|message|text|send(?:\s+to)?|search(?:\s+for)?)\s+([a-zA-Z][\w\s]{1,30}?)(?:\s+(?:on\s+whatsapp|in\s+whatsapp|via\s+whatsapp|$))",
        contact_text,
        re.I,
    )
    contact_name = None
    if contact_m:
        contact_name = contact_m.group(1).strip()
    else:
        search_m = re.search(
            r"\b(?:search|find|look|open)\s+(?:for\s+)?([a-zA-Z][\w\s]{1,25}?)(?:\s+(?:in|on)\s+whatsapp|\s*$)",
            contact_text,
            re.I,
        )
        if search_m:
            contact_name = search_m.group(1).strip()

    if contact_name:
        contact_name = re.sub(r"\s+(?:in|on|via)\s+whatsapp.*", "", contact_name, flags=re.I).strip()
        contact_name = re.sub(r"^(?:to|for|the|a\s+message\s+to)\s+", "", contact_name, flags=re.I).strip()

    search_only = msg_text is None or not msg_text

    return {
        "contact": contact_name if contact_name else None,
        "message": msg_text,
        "search_only": search_only,
    }


def _param_brightness(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    lvl_match = re.search(r"\b(\d{1,3})\s*%?\b", text)
    lvl = int(lvl_match.group(1)) if lvl_match else None
    up = re.search(r"\b(up|increase|brighter)\b", text, re.I)
    dn = re.search(r"\b(down|decrease|dimmer|darker)\b", text, re.I)
    return {"level": lvl, "direction": "up" if up else "down" if dn else "set"}


def _param_translate(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    lang = re.search(r"\b(to|into|in)\s+([a-zA-Z]+)", text, re.I)
    return {"target_language": lang.group(2).lower() if lang else "english"}


def _param_timer(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    num_match = re.search(r"\b(\d{1,3})\b", text)
    num = num_match.group(1) if num_match else "1"
    unit_match = re.search(r"\b(seconds?|minutes?|hours?)\b", text, re.I)
    unit_raw = unit_match.group(1).lower() if unit_match else "minute"
    unit = unit_raw.rstrip("s") if unit_raw.endswith("s") else unit_raw
    return {"duration": int(num), "unit": unit}


def _param_calculate(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    expr = re.sub(
        r".*\b(?:calculate|compute|solve|what(?:\s+is|'s)|today\s+is)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    expr = expr.rstrip("?!. ")
    expr_clean = re.sub(r"\bplus\b", "+", expr, flags=re.I)
    expr_clean = re.sub(r"\bminus\b", "-", expr_clean, flags=re.I)
    expr_clean = re.sub(r"\btimes\b", "*", expr_clean, flags=re.I)
    expr_clean = re.sub(r"\bdivided\s+by\b", "/", expr_clean, flags=re.I)
    expr_clean = re.sub(r"\s+", "", expr_clean)
    return {"expression": expr_clean}


def _param_note(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    note_text = re.sub(
        r".*\b(?:write|take|create|make)\s+(?:a\s+)?(?:note|memo)(?:\s+(?:in|on)\s+notes)?(?:\s+(?:saying|about|that|with))?\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    note_text = note_text.rstrip("?!. ")
    return {"app": "notes", "note_text": note_text}


def _param_weather(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    loc = re.search(r"\b(?:in|for|at)\s+([a-zA-Z][\w\s]{1,40}?)(?:\s*\?|\s*$)", text, re.I)
    return {"location": loc.group(1).strip() if loc else None}


def _param_tab_cleanup(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    close_kw = []
    keep_kw = []
    text_lower = text.lower()
    close_match = re.search(r"\b(?:containing|with|matching|about|of|close)\s+([a-zA-Z0-9\s,]+)\b", text_lower)
    if "keep" in text_lower or "protect" in text_lower:
        keep_match = re.search(r"\b(?:keep|protect|save|except)\s+(?:tabs\s+)?(?:containing|with|matching|about|of)?\s*([a-zA-Z0-9\s,]+)\b", text_lower)
        if keep_match:
            keep_kw = [k.strip() for k in keep_match.group(1).split(",") if k.strip()]
    elif close_match:
        close_kw = [k.strip() for k in close_match.group(1).split(",") if k.strip()]
    return {"close_keywords": close_kw, "keep_keywords": keep_kw}


def _noop(m: re.Match[str] | None, text: str) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Rule definitions — extend by appending to ``ROUTING_RULES``
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingRule:
    """Single routing rule: pattern → intent + parameter extractor."""

    name: str
    pattern: str
    intent: IntentCategory
    base_confidence: float
    extract: ParamExtractor


ROUTING_RULES: list[RoutingRule] = [
    # ── STOP — highest priority in the entire rule list ───────────────────────
    RoutingRule(
        "stop_now",
        r"^(stop|cancel|abort|quit|friday\s+stop|hey\s+friday\s+stop|enough|shut\s+up|be\s+quiet\s+now)[\s!.]*$",
        IntentCategory.STOP,
        0.99,
        _noop,
    ),
    RoutingRule(
        "stop_mid",
        r"\b(stop that|stop now|cancel that|abort that|never\s+mind|forget\s+it|stop\s+everything)\b",
        IntentCategory.STOP,
        0.98,
        _noop,
    ),
    # ── Standing orders ────────────────────────────────────────────────────────
    RoutingRule(
        "standing_order_set",
        r"\b(always|never|from\s+now\s+on|standing\s+order|remember\s+to\s+always|always\s+confirm|stop\s+asking\s+me)\b",
        IntentCategory.STANDING_ORDER,
        0.91,
        _noop,
    ),
    RoutingRule(
        "standing_order_remove",
        r"\b(remove\s+standing\s+order|forget\s+that\s+rule|cancel\s+standing\s+order|stop\s+always)\b",
        IntentCategory.STANDING_ORDER,
        0.93,
        _noop,
    ),
    # ── Conversation (highest priority after STOP) ─────────────────────────────
    RoutingRule("greeting", r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|howdy)\b", IntentCategory.CHAT, 0.92, _noop),
    RoutingRule("thanks", r"^(thanks|thank you|thx|cheers)\b", IntentCategory.CHAT, 0.90, _noop),
    RoutingRule("goodbye", r"^(bye|goodbye|see you|later)\b", IntentCategory.CHAT, 0.88, _noop),
    RoutingRule("how_are_you", r"\bhow are you\b", IntentCategory.CHAT, 0.90, _noop),
    RoutingRule(
        "introduce_self",
        r"\b(introduce\s+(yourself|your\s+self)|who\s+are\s+you|what\s+are\s+you|tell\s+me\s+about\s+(yourself|you)|what\s+do\s+you\s+do)\b",
        IntentCategory.CHAT,
        0.94,
        _noop,
    ),
    RoutingRule("joke", r"\b(?:tell|give|say|hear|crack)\s+(?:me\s+)?(?:a\s+)?joke\b|\bknow\s+any\s+jokes\b|^(?:tell\s+me\s+a\s+)?joke[!?.]*$", IntentCategory.CHAT, 0.97, _noop),
    RoutingRule("speak_aloud", r"\b(speak|say|tell\s+me)\s+(something|a\s+joke|a\s+story|out\s+loud|aloud)\b", IntentCategory.CHAT, 0.93, _noop),
    RoutingRule("speak_verb", r"^(speak|say)\b", IntentCategory.CHAT, 0.91, _noop),
    # ── Media transport (before generic play) ─────────────────────────────
    RoutingRule("next_track", r"\b((play|hit)\s+)?(next|skip)(\s+track|\s+song)?\b", IntentCategory.NEXT_TRACK, 0.95, _noop),
    RoutingRule("prev_track", r"\b(previous|prev|last)\s+(track|song)\b", IntentCategory.PREV_TRACK, 0.93, _noop),
    RoutingRule("pause_media", r"\b(pause|stop)\s+(music|playback|song|track)?\b", IntentCategory.PAUSE_MEDIA, 0.90, _noop),
    RoutingRule("resume_media", r"\b(resume|unpause)\s*(music|playback|song)?\b", IntentCategory.PLAY_MEDIA, 0.88, _noop),
    # ── Mouse / keyboard ──────────────────────────────────────────────────
    RoutingRule("double_click", r"\b(double.?click|dbl.?click)\b", IntentCategory.MOUSE_CLICK, 0.97, _param_mouse_click),
    RoutingRule("right_click", r"\bright.?click\b", IntentCategory.MOUSE_CLICK, 0.97, _param_mouse_click),
    RoutingRule("click_coords", r"\bclick\s+(?:at\s+)?(?P<x>\d{2,4})\s*[, ]\s*(?P<y>\d{2,4})\b", IntentCategory.MOUSE_CLICK, 0.96, _param_mouse_click),
    RoutingRule("click_named", r"\b(click|tap|press)\b.*(button|icon|link|item|(?P<loc>[\w\s]{2,}))", IntentCategory.MOUSE_CLICK, 0.93, _param_mouse_click),
    RoutingRule("move_coords", r"\bmove\s+(?:the\s+)?(mouse|cursor)\s+(?:to\s+)?(?P<x>\d{2,4})\s*[, ]\s*(?P<y>\d{2,4})\b", IntentCategory.MOUSE_MOVE, 0.94, _param_mouse_move),
    RoutingRule("move_mouse", r"\b(move|go to|hover).*(mouse|cursor|pointer)\b", IntentCategory.MOUSE_MOVE, 0.88, _param_mouse_move),
    RoutingRule("hotkey_actions", r"\b(copy|paste|undo|redo|select all|save|refresh|reload|new tab|close tab|switch tab|alt tab|fullscreen|zoom in|zoom out)\b", IntentCategory.KEYBOARD_HOTKEY, 0.91, _param_hotkey),
    RoutingRule("scroll", r"\bscroll\s*(?P<amt>\d{1,2})?\s*(up|down)?\b", IntentCategory.MOUSE_SCROLL, 0.93, _param_scroll),
    RoutingRule("drag", r"\b(drag)\b.*\b(to|from)\b", IntentCategory.MOUSE_DRAG, 0.88, _noop),
    RoutingRule("type_text", r"\b(type|write|enter|input)\s+", IntentCategory.KEYBOARD_TYPE, 0.91, _param_keyboard_type),
    RoutingRule("hotkey_combo", r"\b(press|hit)\s+((?:ctrl|alt|shift|win|cmd)(?:\s*\+\s*\w+)+)\b", IntentCategory.KEYBOARD_HOTKEY, 0.96, _param_hotkey),
    RoutingRule("hotkey_single", r"\b(press|hit)\s+(ctrl|alt|shift|win|cmd|tab|enter|escape|esc|delete|f\d+)\b", IntentCategory.KEYBOARD_HOTKEY, 0.94, _param_hotkey),
    # ── Screen / clipboard ────────────────────────────────────────────────
    RoutingRule("screenshot_verb", r"\b(take|capture|grab)\s+(a\s+)?(screenshot|screen\s*shot|snap)\b", IntentCategory.SCREEN_CAPTURE, 0.96, _noop),
    RoutingRule("screenshot", r"\b(screenshot|screen\s*capture)\b", IntentCategory.SCREEN_CAPTURE, 0.94, _noop),
    RoutingRule("screen_read", r"\b(read|what'?s?\s+on|what is on|scan)\s+.*(screen|window|display)\b", IntentCategory.SCREEN_READ, 0.91, _noop),
    RoutingRule("clipboard_copy", r"\b(copy to clipboard|copy that)\b", IntentCategory.CLIPBOARD_COPY, 0.93, _noop),
    RoutingRule("clipboard_paste", r"\b(paste from clipboard|paste that)\b", IntentCategory.CLIPBOARD_PASTE, 0.93, _noop),
    RoutingRule("clipboard_read", r"\b(read|what'?s?\s+in)\s+(the\s+)?clipboard\b", IntentCategory.CLIPBOARD_COPY, 0.88, _noop),
    # ── Window management ─────────────────────────────────────────────────
    RoutingRule("window_focus", r"\b(focus|switch to|bring up|show)\s+(?P<app>[\w\s.+]+?)\s*(window|app)?\b", IntentCategory.WINDOW_FOCUS, 0.89, _param_open_app),
    RoutingRule("window_close", r"\b(close|quit|exit)\s+(?P<app>[\w\s.+]+?)\s*(window|app)?\b", IntentCategory.WINDOW_CLOSE, 0.92, _param_open_app),
    RoutingRule("window_minimize", r"\b(minimize|minimise|hide)\s+(?:the\s+)?(?:window|app)?\b", IntentCategory.WINDOW_CLOSE, 0.82, _noop),
    # ── Apps & search ─────────────────────────────────────────────────────
    RoutingRule(
        "new_project",
        r"\b(?:i\s+)?(?:want|wanna|would\s+like)\s+to\s+(?:work\s+on|start|begin|create)\s+(?:a\s+)?(?:new|fresh|blank)\s+project\b",
        IntentCategory.OPEN_APP,
        0.97,
        _param_new_project,
    ),
    RoutingRule(
        "new_project_short",
        r"\b(?:let'?s\s+)?(?:work\s+on|start|begin|create)\s+(?:a\s+)?(?:new|fresh|blank)\s+project\b",
        IntentCategory.OPEN_APP,
        0.96,
        _param_new_project,
    ),
    RoutingRule(
        "vscode_new_project",
        r"\bopen\s+(?:vs\s*code|visual\s+studio\s+code|vscode)\b.*\b(?:new|fresh|blank)\s+(?:project|workspace)\b",
        IntentCategory.OPEN_APP,
        0.97,
        _param_new_project,
    ),
    RoutingRule(
        "vscode_new_project_alt",
        r"\b(?:new|fresh|blank)\s+(?:project|workspace)\b.*\b(?:in\s+)?(?:vs\s*code|visual\s+studio\s+code|vscode)\b",
        IntentCategory.OPEN_APP,
        0.97,
        _param_new_project,
    ),
    RoutingRule("open_app_generic", r"\b(open|launch|start|run)\s+(?P<app>[\w\s.+]+?)\s*$", IntentCategory.OPEN_APP, 0.93, _param_open_app),
    RoutingRule("open_app_named", r"\b(open|launch|start|run)\s+(?P<app>chrome|notepad|calculator|spotify|discord|vscode|terminal|whatsapp|files|explorer|settings|file explorer)\b", IntentCategory.OPEN_APP, 0.91, _param_open_app),
    RoutingRule(
        "search_compound",
        r"\b(search\b.*\band\s+scroll|search\s+and\s+(open|browse|read|scroll)|browse\s+.*\band\s+scroll)\b",
        IntentCategory.SEARCH_WEB,
        0.94,
        _param_search,
    ),
    RoutingRule("search_web", r"\b(google|search( for| on web)?|look up|find)\s+", IntentCategory.SEARCH_WEB, 0.88, _param_search),
    RoutingRule("tab_cleanup", r"\b(clean\s*up|close|cleanup|clear|remove|kill)\s+(browser\s+)?(tabs|tab|duplicates|blank\s+tabs)\b", IntentCategory.TAB_CLEANUP, 0.94, _param_tab_cleanup),
    # ── Music (platform-specific before generic) ──────────────────────────
    RoutingRule("play_spotify", r"\b(play|start)\b.*\b(on|in)\s+spotify\b", IntentCategory.PLAY_MEDIA, 0.97, _param_play_music),
    RoutingRule("play_yt_music", r"\b(play|start)\b.*\b(on|in)\s+(youtube\s+music|yt\s+music)\b", IntentCategory.PLAY_MEDIA, 0.97, _param_play_music),
    RoutingRule("play_youtube", r"\b(play|start)\b.*\b(on|in)\s+youtube\b", IntentCategory.PLAY_MEDIA, 0.96, _param_play_music),
    RoutingRule("play_garage", r"\bplay\s+garage\s+music\b", IntentCategory.PLAY_MEDIA, 0.96, _param_play_music),
    RoutingRule("play_some_music", r"\bplay\s+(some\s+)?music\b", IntentCategory.PLAY_MEDIA, 0.93, _param_play_music),
    RoutingRule("play_song", r"\b(play|start)\b(?!\s+(the\s+)?(next|previous|prev|last)\b).*?\b(music|song|track)\b", IntentCategory.PLAY_MEDIA, 0.94, _param_play_music),
    RoutingRule("play_title", r"\b(play|start)\b(?!\s+(the\s+)?(next|previous|prev|last)\b(\s+track)?)\s+[\w\s'\"-]{2,}", IntentCategory.PLAY_MEDIA, 0.90, _param_play_music),
    RoutingRule("open_youtube", r"\b(open|launch|start|run)\s+(?:the\s+)?(?:you\s*tube|youtube)\b", IntentCategory.OPEN_YOUTUBE, 0.97, _param_youtube),
    RoutingRule("search_youtube", r"\b(search|watch)\s+.*\bon\s+youtube\b", IntentCategory.OPEN_YOUTUBE, 0.87, _param_youtube),
    # ── Communication ─────────────────────────────────────────────────────
    RoutingRule(
        "whatsapp_confirm_send",
        r"\b(?:yes\s+)?(?:send\s+it|send\s+the\s+message|proceed\s+and\s+send|send\s+to\s+(?:sathish|satish)|yes\s+send)\b",
        IntentCategory.KEYBOARD_HOTKEY,
        0.98,
        lambda m, t: {"keys": ["enter"]},
    ),
    RoutingRule(
        "whatsapp_search",
        r"\b(?:search|find|look)\s+(?:for\s+)?(?P<contact>[a-zA-Z][\w\s]{1,25})\s+(?:in|on)\s+whatsapp\b|\bopen\s+whatsapp\s+and\s+search\s+(?:for\s+)?(?P<contact2>[a-zA-Z][\w\s]{1,25})\b",
        IntentCategory.OPEN_WHATSAPP,
        0.95,
        _param_whatsapp,
    ),
    RoutingRule("whatsapp_send", r"\b(send|message|text)\b.*\bwhatsapp\b", IntentCategory.OPEN_WHATSAPP, 0.90, _param_whatsapp),
    RoutingRule("whatsapp_open", r"\bwhatsapp\b.*\b(message|text|send)\b", IntentCategory.OPEN_WHATSAPP, 0.88, _param_whatsapp),
    RoutingRule(
        "message_contact_whatsapp",
        r"(?=.*\b(?:whatsapp|on\s+whatsapp|via\s+whatsapp)\b).*\b(send|message|text)\s+(?:to\s+)?(?P<contact>[a-zA-Z][\w\s]{1,25})\b",
        IntentCategory.OPEN_WHATSAPP,
        0.78,
        _param_whatsapp,
    ),
    # ── Friday presence (before OS sleep — higher priority) ─────────────────
    RoutingRule(
        "presence_sleep_timed",
        r"\bgive me\s+(?:an?\s+)?\d+\s*(hour|minute|min|second)s?\b",
        IntentCategory.PRESENCE_MODE,
        0.98,
        _noop,
    ),
    RoutingRule(
        "presence_sleep_phrase",
        r"\b(go to sleep|sleep mode|leave me alone|shut up for|stop listening|go away|be quiet for)\b",
        IntentCategory.PRESENCE_MODE,
        0.97,
        _noop,
    ),
    RoutingRule(
        "presence_quiet",
        r"\b(quiet mode|just watch|watch mode|be less chatty|only speak when|stop talking unless)\b",
        IntentCategory.PRESENCE_MODE,
        0.96,
        _noop,
    ),
    RoutingRule(
        "presence_wake",
        r"\b(i('m| am) back|come back|wake up|resume listening|start listening again|resident mode)\b",
        IntentCategory.PRESENCE_MODE,
        0.97,
        _noop,
    ),
    # ── System ────────────────────────────────────────────────────────────
    RoutingRule("volume_adjust", r"\b(volume|sound)\b.*(up|down|set|mute|unmute|\d+)", IntentCategory.VOLUME_SET, 0.93, _param_volume),
    RoutingRule("volume_set", r"\b(set|change)\s+volume\s+to\s+\d+", IntentCategory.VOLUME_SET, 0.94, _param_volume),
    RoutingRule("louder_quieter", r"\b(louder|quieter|turn (it )?up|turn (it )?down)\b", IntentCategory.VOLUME_SET, 0.86, _param_volume),
    RoutingRule("mute", r"\b(mute|unmute)\b", IntentCategory.VOLUME_MUTE, 0.94, _noop),
    RoutingRule("brightness", r"\b(brightness|screen brightness)\b", IntentCategory.BRIGHTNESS, 0.90, _param_brightness),
    RoutingRule("system_info", r"\b(system info|cpu|ram|memory usage|disk usage|system stats|how much ram)\b", IntentCategory.SYSTEM_INFO, 0.91, _noop),
    RoutingRule("shutdown", r"\b(shutdown|shut down|power off)\b", IntentCategory.SHUTDOWN, 0.98, _noop),
    RoutingRule("restart", r"\b(restart|reboot)\b", IntentCategory.RESTART, 0.97, _noop),
    RoutingRule("sleep", r"\b(sleep|hibernate|suspend)\b", IntentCategory.SLEEP, 0.95, _noop),
    RoutingRule("lock", r"\b(lock|lock screen|lock pc|lock computer)\b", IntentCategory.LOCK, 0.95, _noop),
    RoutingRule("play_toggle", r"\b(play|resume)\b(?!\s+[\w])", IntentCategory.PLAY_MEDIA, 0.82, _noop),
    # ── Knowledge / conversation ──────────────────────────────────────────
    RoutingRule("news_read", r"\b(read|get|show)\s+(the\s+)?(news|headlines)\b", IntentCategory.NEWS, 0.94, _param_news),
    RoutingRule("news", r"\b(news|headlines|what'?s happening)\b", IntentCategory.NEWS, 0.91, _param_news),
    RoutingRule("smart_search", r"\b(smart search|look up)\b", IntentCategory.EXPLAIN, 0.86, _param_search),
    RoutingRule("explain", r"\b(explain|what is|what's|define|tell me about|who is|who's)\b", IntentCategory.EXPLAIN, 0.90, _noop),
    RoutingRule("summarise", r"\b(summarise|summarize|tldr|summary of)\b", IntentCategory.SUMMARISE, 0.88, _noop),
    RoutingRule(
        "write_note",
        r"\b(?:write|take|create|make)\s+(?:a\s+)?(?:note|memo)\b|\bnote\s+down\b",
        IntentCategory.OPEN_APP,
        0.96,
        _param_note,
    ),
    RoutingRule("calculate_verb", r"\b(calculate|compute|math)\b", IntentCategory.CALCULATE, 0.90, _param_calculate),
    RoutingRule(
        "calculate_query",
        r"\b(?:calculate|compute|solve|what(?:'s|\s+is)|today\s+is)\s+\d+\s*(?:\+|\-|\*|\/|plus|minus|times|divided\s+by)\s*\d+\b|\b\d+\s*(?:\+|\-|\*|\/|plus|minus|times|divided\s+by)\s*\d+\b",
        IntentCategory.CALCULATE,
        0.96,
        _param_calculate,
    ),
    RoutingRule("calculate_expr", r"\bwhat(?:'s| is)\s+[\d\s+\-*/().]+", IntentCategory.CALCULATE, 0.92, _param_calculate),
    RoutingRule("translate", r"\b(translate|in (spanish|french|german|hindi|telugu|japanese|tamil))\b", IntentCategory.TRANSLATE, 0.90, _param_translate),
    RoutingRule("write_text", r"\b(write|draft|compose)\b", IntentCategory.WRITE_TEXT, 0.83, _noop),
    RoutingRule("code_help", r"\b(code|debug|fix|program|script)\b", IntentCategory.CODE_HELP, 0.85, _noop),
    RoutingRule("weather", r"\b(weather|temperature|forecast)\b", IntentCategory.WEATHER, 0.93, _param_weather),
    RoutingRule("time_date_full", r"\b(what time|what's the time|current time|what date|what day|what'?s today)\b", IntentCategory.TIME_DATE, 0.96, _noop),
    RoutingRule("time_date_short", r"\b(time|date|day)\b", IntentCategory.TIME_DATE, 0.88, _noop),
    RoutingRule("reminder", r"\b(remind|reminder|set reminder)\b", IntentCategory.REMINDER, 0.92, _noop),
    RoutingRule("timer", r"\b(timer|set timer|countdown|alarm)\b", IntentCategory.TIMER, 0.93, _param_timer),
]

# Backward-compatible tuple format used by tests and legacy imports.
RULES: list[tuple[str, IntentCategory, float, ParamExtractor]] = [
    (rule.pattern, rule.intent, rule.base_confidence, rule.extract) for rule in ROUTING_RULES
]

_REQUIRED_PARAMS: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.KEYBOARD_TYPE: ("text",),
    IntentCategory.OPEN_APP: ("app",),
    IntentCategory.SEARCH_WEB: ("query",),
    IntentCategory.OPEN_YOUTUBE: (),
    IntentCategory.TIMER: ("duration",),
}

_INTENT_HINTS: dict[IntentCategory, str] = {
    IntentCategory.CHAT: "General conversation or greeting",
    IntentCategory.OPEN_APP: "params: {app}",
    IntentCategory.KEYBOARD_TYPE: "params: {text}",
    IntentCategory.KEYBOARD_HOTKEY: "params: {keys: list}",
    IntentCategory.SEARCH_WEB: "params: {query}",
    IntentCategory.PLAY_MEDIA: "params: {song, platform}",
    IntentCategory.VOLUME_SET: "params: {level, direction}",
    IntentCategory.TIMER: "params: {duration, unit}",
    IntentCategory.EXPLAIN: "Factual Q&A — no tool execution needed",
    IntentCategory.NEWS: "News headlines request",
    IntentCategory.TIME_DATE: "Current time or date",
    IntentCategory.OPEN_WHATSAPP: "params: {contact, message}",
    IntentCategory.MOUSE_SCROLL: "params: {amount, direction}",
    IntentCategory.SCREEN_CAPTURE: "Take a screenshot",
    IntentCategory.TAB_CLEANUP: "params: {close_keywords: list, keep_keywords: list}",
    IntentCategory.SHUTDOWN: "Requires user confirmation",
}


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------


class RuleEngine:
    """Scores and ranks compiled routing rules against user text."""

    def __init__(self, rules: list[RoutingRule] | None = None) -> None:
        source = rules or ROUTING_RULES
        self._compiled: list[tuple[re.Pattern[str], RoutingRule, int]] = [
            (re.compile(rule.pattern, re.IGNORECASE | re.DOTALL), rule, idx)
            for idx, rule in enumerate(source)
        ]

    def classify(self, text: str) -> ClassificationResult:
        text = text.strip()
        if not text:
            return ClassificationResult(IntentCategory.CHAT, 0.50, {}, "rule")

        # Guard: How-to questions ("How to close tabs", "Tell me how to...", "What is the shortcut")
        # are requests for explanation/instructions, NOT action execution commands!
        if re.search(
            r"\b(?:how\s+to|how\s+do\s+i|how\s+can\s+i|tell\s+me\s+how|explain\s+how|what\s+is\s+the\s+(?:shortcut|key|command|way))\b",
            text,
            re.I,
        ):
            return ClassificationResult(
                intent=IntentCategory.EXPLAIN,
                confidence=0.92,
                params={"query": text},
                source="rule",
                matched_rule="how_to_explanation",
            )

        candidates: list[tuple[float, int, RoutingRule, dict[str, Any], re.Match[str]]] = []

        for pattern, rule, priority in self._compiled:
            match = pattern.search(text)
            if not match:
                continue
            try:
                params = rule.extract(match, text)
            except Exception as exc:
                logger.debug("Param extractor failed for %s: %s", rule.name, exc)
                params = {}
            conf = self._score(rule, params, text, match, priority)
            candidates.append((conf, priority, rule, params, match))

        if not candidates:
            return self._chat_or_unknown_fallback(text)

        candidates.sort(key=lambda item: (-item[0], item[1]))
        conf, _, rule, params, _ = candidates[0]
        return ClassificationResult(
            intent=rule.intent,
            confidence=conf,
            params=params,
            source="rule",
            matched_rule=rule.name,
        )

    @staticmethod
    def _score(
        rule: RoutingRule,
        params: dict[str, Any],
        text: str,
        match: re.Match[str],
        priority: int,
    ) -> float:
        conf = rule.base_confidence
        coverage = len(match.group(0)) / max(len(text), 1)
        if coverage > 0.55:
            conf += 0.04
        elif coverage < 0.15 and len(text) > 20:
            conf -= 0.06
        if not _has_required_params(rule.intent, params):
            conf -= 0.22
        conf -= priority * 0.0005
        if rule.intent == IntentCategory.SEARCH_WEB and _VAGUE_SEARCH_RE.search(params.get("query", "")):
            conf -= 0.12
        if rule.intent == IntentCategory.MOUSE_SCROLL and re.search(r"\bsearch\b", text, re.I):
            conf -= 0.18
        if rule.intent == IntentCategory.KEYBOARD_HOTKEY and re.search(r"\brefresh\b", text, re.I):
            conf += 0.03
        if rule.intent == IntentCategory.OPEN_APP and re.search(r"\byou\s*tube\b", text, re.I):
            conf -= 0.25
        return _clamp_confidence(conf)

    @staticmethod
    def _chat_or_unknown_fallback(text: str) -> ClassificationResult:
        stripped = text.strip()
        if not stripped:
            return ClassificationResult(IntentCategory.CHAT, 0.50, {}, "fallback")
        if _GREETING_RE.match(stripped):
            return ClassificationResult(IntentCategory.CHAT, 0.88, {}, "fallback")
        if _THANKS_RE.match(stripped):
            return ClassificationResult(IntentCategory.CHAT, 0.86, {}, "fallback")
        if stripped.endswith("?"):
            return ClassificationResult(IntentCategory.EXPLAIN, 0.68, {}, "fallback")
        if len(stripped.split()) <= 3:
            return ClassificationResult(IntentCategory.CHAT, CHAT_DEFAULT_CONFIDENCE, {}, "fallback")
        return ClassificationResult(IntentCategory.CHAT, UNKNOWN_FALLBACK_CONFIDENCE, {}, "fallback")


_default_engine = RuleEngine()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp_confidence(value: float) -> float:
    return max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, round(value, 3)))


def _has_required_params(intent: IntentCategory, params: dict[str, Any]) -> bool:
    required = _REQUIRED_PARAMS.get(intent, ())
    return all(params.get(key) for key in required)


def _rule_classify_static(text: str) -> ClassificationResult:
    """Core rule engine — shared by IntentRouter and keyword fallback."""
    return _default_engine.classify(text)


def _normalize_llm_params(intent: IntentCategory, params: dict[str, Any], text: str) -> dict[str, Any]:
    params = dict(params or {})
    if intent == IntentCategory.KEYBOARD_TYPE and not params.get("text"):
        params.update(_param_keyboard_type(None, text))
    if intent == IntentCategory.OPEN_APP and not params.get("app"):
        params.update(_param_open_app(None, text))
    if intent in (IntentCategory.SEARCH_WEB, IntentCategory.EXPLAIN, IntentCategory.NEWS) and not params.get("query"):
        params.update(_param_search(None, text))
    if intent == IntentCategory.PLAY_MEDIA and not params.get("song"):
        params.update(_param_play_music(None, text))
    if intent == IntentCategory.TIMER and not params.get("duration"):
        params.update(_param_timer(None, text))
    if intent == IntentCategory.OPEN_WHATSAPP and not params.get("contact"):
        params.update(_param_whatsapp(None, text))
    if intent == IntentCategory.KEYBOARD_HOTKEY and not params.get("keys"):
        params.update(_param_hotkey(None, text))
    return params


def _merge_results(
    rule: ClassificationResult,
    llm: ClassificationResult | None,
    text: str,
) -> ClassificationResult:
    if llm is None:
        return rule
    if llm.confidence > rule.confidence + 0.05:
        llm.params = _normalize_llm_params(llm.intent, llm.params, text)
        if not _has_required_params(llm.intent, llm.params) and _has_required_params(rule.intent, rule.params):
            llm.params = {**rule.params, **llm.params}
        if not _has_required_params(llm.intent, llm.params):
            llm.confidence = _clamp_confidence(llm.confidence - 0.15)
        return llm
    if llm.intent == rule.intent and llm.params:
        rule.params = {**rule.params, **llm.params}
    return rule


def _keyword_fallback(text: str) -> ClassificationResult:
    result = _rule_classify_static(text)
    if result.confidence > CHAT_DEFAULT_CONFIDENCE:
        return result
    return RuleEngine._chat_or_unknown_fallback(text)


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """Robust JSON extraction from LLM output."""
    cleaned = raw.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = cleaned.split("```")[0].strip()

    for candidate in (cleaned,):
        try:
            data = json.loads(candidate)
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
    return None


# ---------------------------------------------------------------------------
# Intent router (rules + optional LLM)
# ---------------------------------------------------------------------------


class IntentRouter:
    """
    Hybrid intent classifier: rules first, LLM when confidence is below threshold.

    LangGraph calls ``_rule_classify`` directly for the 0.88 fast-path, then
    ``classify`` for gray-zone disambiguation.
    """

    def __init__(self, llm_skip_threshold: float = DEFAULT_LLM_SKIP_THRESHOLD):
        self.llm_skip_threshold = llm_skip_threshold
        self._engine = _default_engine

    async def classify(
        self,
        text: str,
        context: list[dict] | None = None,
        screen_context: str | None = None,
        active_window: str | None = None,
    ) -> dict[str, Any]:
        rule_result = self._rule_classify(text)

        if rule_result.confidence >= self.llm_skip_threshold:
            logger.debug(
                "Rule-only classify: %s (%.2f, rule=%s)",
                rule_result.intent.value,
                rule_result.confidence,
                rule_result.matched_rule,
            )
            return rule_result.to_dict()

        llm_result = await self._llm_classify(text, context, screen_context, active_window, rule_result)
        merged = _merge_results(rule_result, llm_result, text)
        return merged.to_dict()

    def classify_rules(self, text: str) -> ClassificationResult:
        """Synchronous rule-only classification (no LLM)."""
        return self._rule_classify(text)

    def _rule_classify(self, text: str) -> ClassificationResult:
        return self._engine.classify(text)

    async def _llm_classify(
        self,
        text: str,
        context: list[dict] | None,
        screen_context: str | None,
        active_window: str | None,
        rule_hint: ClassificationResult,
    ) -> ClassificationResult | None:
        prompt = self._build_llm_prompt(text, context, screen_context, active_window, rule_hint)
        raw = await self._call_llm(prompt)
        if not raw:
            return _keyword_fallback(text)

        parsed = _parse_llm_json(raw)
        if not parsed:
            logger.warning("LLM classify returned unparseable JSON — keyword fallback")
            return _keyword_fallback(text)

        intent_str = str(parsed.get("intent", "chat")).strip().lower()
        try:
            intent = IntentCategory(intent_str)
        except ValueError:
            intent = IntentCategory.CHAT

        confidence = _clamp_confidence(float(parsed.get("confidence", 0.72)))
        params = _normalize_llm_params(intent, parsed.get("params", {}), text)

        if not _has_required_params(intent, params):
            confidence = _clamp_confidence(confidence - 0.18)

        return ClassificationResult(intent=intent, confidence=confidence, params=params, source="llm")

    def _build_llm_prompt(
        self,
        text: str,
        context: list[dict] | None,
        screen_context: str | None,
        active_window: str | None,
        rule_hint: ClassificationResult,
    ) -> str:
        intent_lines = []
        for intent in IntentCategory:
            hint = _INTENT_HINTS.get(intent, "")
            suffix = f" — {hint}" if hint else ""
            intent_lines.append(f"- {intent.value}{suffix}")

        ctx_parts: list[str] = []
        if active_window:
            ctx_parts.append(f"Active window: {active_window}")
        if screen_context:
            ctx_parts.append(f"Screen snippet: {screen_context[:200]}")
        if context:
            recent = " | ".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:80]}"
                for m in context[-3:]
            )
            if recent:
                ctx_parts.append(f"Recent dialogue: {recent}")
        ctx_parts.append(
            f"Rule guess: {rule_hint.intent.value} ({rule_hint.confidence:.2f}) "
            f"params={rule_hint.params} rule={rule_hint.matched_rule}"
        )

        return (
            "Classify the user request into exactly ONE intent from the list.\n"
            "Respond ONLY with JSON: {\"intent\": \"...\", \"confidence\": 0.0-1.0, \"params\": {...}}\n"
            "Confidence: 0.9+ explicit command, 0.7–0.85 inferred, <0.6 uncertain.\n"
            "Extract concrete params; NEVER leave query/text/app empty when implied.\n"
            "Rewrite vague search terms into specific queries.\n"
            "Prefer rule guess when confidence is similar unless another intent is clearly better.\n\n"
            "Examples:\n"
            '{"intent":"open_app","confidence":0.93,"params":{"app":"chrome"}}\n'
            '{"intent":"search_web","confidence":0.88,"params":{"query":"latest AI news"}}\n'
            '{"intent":"keyboard_type","confidence":0.91,"params":{"text":"hello world"}}\n'
            '{"intent":"chat","confidence":0.85,"params":{}}\n\n'
            f"Intents:\n" + "\n".join(intent_lines) + "\n\n"
            + "\n".join(ctx_parts) + "\n"
            f"User: {text}"
        )

    async def _call_llm(self, prompt: str) -> str | None:
        try:
            from brain.groq_client import groq_complete

            return await groq_complete(prompt, max_tokens=220)
        except Exception as exc:
            logger.warning("LLM classify call failed: %s", exc)
            return None

    def build_clarification_prompt(self, intent: IntentCategory, params: dict[str, Any]) -> str:
        prompts = {
            IntentCategory.MOUSE_CLICK: "Where should I click? Describe the button or location.",
            IntentCategory.KEYBOARD_TYPE: "What would you like me to type?",
            IntentCategory.OPEN_APP: "Which app should I open?",
            IntentCategory.VOLUME_SET: "What volume level (0–100), or up/down?",
            IntentCategory.OPEN_YOUTUBE: "What should I search on YouTube?",
            IntentCategory.OPEN_WHATSAPP: "Who should I message, and what should I say?",
            IntentCategory.TIMER: "How long should I set the timer for?",
            IntentCategory.REMINDER: "What should I remind you about, and when?",
            IntentCategory.TRANSLATE: "What should I translate, and into which language?",
            IntentCategory.SEARCH_WEB: "What would you like me to search for?",
            IntentCategory.PLAY_MEDIA: "What song should I play, and on which platform?",
        }
        return prompts.get(intent, "Could you clarify what you'd like me to do?")


_router_instance: IntentRouter | None = None


def get_router() -> IntentRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = IntentRouter()
    return _router_instance


async def route_command(text: str) -> tuple[str | None, dict[str, Any] | None]:
    result = await get_router().classify(text)
    intent_value = result["intent"].value if result["intent"] != IntentCategory.CHAT else None
    return intent_value, result["params"] or None


# Legacy alias for compiled rules (some tests may reference this).
_COMPILED_RULES = [
    (re.compile(rule.pattern, re.IGNORECASE | re.DOTALL), rule.intent, rule.base_confidence, rule.extract, idx)
    for idx, rule in enumerate(ROUTING_RULES)
]
