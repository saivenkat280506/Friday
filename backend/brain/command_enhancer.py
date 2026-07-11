"""
command_enhancer.py — Pre-classification command enhancement for FRIDAY.
=========================================================================
Sits between ``perceive`` and ``classify`` in the LangGraph pipeline.

Responsibilities
----------------
1. **Category tagging** — lightweight keyword scan to tag the broad category
   (media, communication, information, system, general).
2. **Context enrichment** — fills in missing defaults:
   - Music / video queries without a platform → default to YouTube.
   - News / headline requests → tag with extracted topic.
   - WhatsApp commands → resolve contact from phonebook, attach phone number.
3. **Enhanced output** — produces ``EnhancedCommand`` with rewritten input,
   pre-extracted params, category tag, and classifier hints.

Design: rule-based only (no LLM call) so it adds < 1 ms to the hot path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("friday.enhancer")

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EnhancedCommand:
    """Output of the command enhancer."""

    enhanced_input: str
    """Possibly rewritten command text (same as original if no rewrite needed)."""

    enhanced_params: dict[str, Any] = field(default_factory=dict)
    """Pre-extracted parameters: contact, phone, platform, query, etc."""

    category: str = "general"
    """Broad category: media, communication, information, system, general."""

    hints: list[str] = field(default_factory=list)
    """Free-form hints for the classifier / planner."""


# ---------------------------------------------------------------------------
# Keyword patterns (compiled once)
# ---------------------------------------------------------------------------

# WhatsApp
_WHATSAPP_RE = re.compile(
    r"\b(whatsapp|whats\s*app|watsapp|what'?s?\s*app)\b", re.I
)
_WA_ACTION_RE = re.compile(
    r"\b(send|message|text|msg|chat|contact|call|open)\b", re.I
)
_WA_CONTACT_RE = re.compile(
    r"""
    (?:(?:send|message|text|msg)\s+(?:to\s+)?
       |(?:to|contact|message)\s+)
    (?P<contact>[a-zA-Z][a-zA-Z\s]{0,30}?)
    (?:\s+(?:on|in|via|through)\s+(?:whatsapp|whats\s*app)
       |\s+(?:saying|and\s+say|that|,|$))
    """,
    re.I | re.X,
)
_WA_CONTACT_AFTER_WA_RE = re.compile(
    r"""
    (?:whatsapp|whats\s*app)\s+
    (?:(?:send|message|text|msg)\s+(?:to\s+)?)
    (?P<contact>[a-zA-Z][a-zA-Z\s]{0,30}?)
    (?:\s+(?:saying|and\s+say|that|,|$))
    """,
    re.I | re.X,
)
_WA_MESSAGE_RE = re.compile(
    r'(?:saying|say|that|message)\s+["\']?(?P<msg>.+?)["\']?\s*$',
    re.I,
)

# Media / music
_MUSIC_KEYWORDS_RE = re.compile(
    r"\b(music|song|track|beat|album|playlist|tune|melody|anthem)\b", re.I
)
_PLAY_RE = re.compile(r"\b(play|listen|start|stream)\b", re.I)
_PLATFORM_RE = re.compile(
    r"\b(?:on|in)\s+(youtube|spotify|yt\s+music|youtube\s+music|soundcloud|apple\s+music)\b",
    re.I,
)
_SEARCH_RE = re.compile(r"\b(search|find|look\s+up|browse)\b", re.I)

# News / information
_NEWS_RE = re.compile(
    r"\b(news|headlines|headline|breaking\s+news|current\s+events)\b", re.I
)
_READ_RE = re.compile(r"\b(read|get|show|tell|fetch|give)\b", re.I)
_EXPLAIN_RE = re.compile(
    r"\b(what\s+is|what'?s|who\s+is|who'?s|explain|define|tell\s+me\s+about)\b", re.I
)

# System
_SYSTEM_RE = re.compile(
    r"\b(shutdown|restart|reboot|sleep|hibernate|lock|volume|brightness|mute|unmute)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------


def _detect_category(text: str) -> str:
    """Classify into broad category via keyword scan."""
    if _WHATSAPP_RE.search(text):
        return "communication"
    if _NEWS_RE.search(text):
        return "information"
    if _EXPLAIN_RE.search(text):
        return "information"
    if _MUSIC_KEYWORDS_RE.search(text) or _PLAY_RE.search(text):
        return "media"
    if _SYSTEM_RE.search(text):
        return "system"
    if _SEARCH_RE.search(text):
        # Determine if this is a media search or web search
        if _MUSIC_KEYWORDS_RE.search(text):
            return "media"
        return "information"
    return "general"


# ---------------------------------------------------------------------------
# WhatsApp enrichment
# ---------------------------------------------------------------------------


def _extract_whatsapp_contact(text: str) -> str | None:
    """Pull the contact name from a WhatsApp command.

    Priority-ordered patterns to handle common phrasings:
      - send message to <NAME> on whatsapp saying ...
      - message <NAME> on whatsapp saying ...
      - whatsapp send/message to <NAME> saying ...
      - send <NAME> a message on whatsapp
      - text <NAME> on whatsapp
    """
    _STOP_WORDS = {
        "a", "an", "the", "on", "in", "via", "through",
        "message", "text", "whatsapp", "saying", "and", "to",
        "send", "msg", "chat", "contact", "that",
    }

    # Pattern 1: "... to <NAME> on/in/via whatsapp ..."
    m = re.search(
        r"\bto\s+(?P<contact>[a-zA-Z][a-zA-Z ]{0,25}?)"
        r"\s+(?:on|in|via|through)\s+(?:whatsapp|whats\s*app)\b",
        text, re.I,
    )
    if m:
        name = m.group("contact").strip()
        if name.lower() not in _STOP_WORDS:
            return name

    # Pattern 2: "... to <NAME> saying/that ..."
    m = re.search(
        r"\bto\s+(?P<contact>[a-zA-Z][a-zA-Z ]{0,25}?)"
        r"\s+(?:saying|and\s+say|that)\s+",
        text, re.I,
    )
    if m:
        name = m.group("contact").strip()
        # Remove trailing "on whatsapp" etc. from the captured name
        name = re.sub(r"\s+(?:on|in|via)\s+(?:whatsapp|whats\s*app)\s*$", "", name, flags=re.I).strip()
        if name.lower() not in _STOP_WORDS:
            return name

    # Pattern 3: "whatsapp <action> <NAME> ..."
    m = re.search(
        r"(?:whatsapp|whats\s*app)\s+"
        r"(?:send|message|text|msg)\s+"
        r"(?:to\s+)?(?P<contact>[a-zA-Z][a-zA-Z ]{0,25}?)"
        r"(?:\s+(?:saying|that|,|$))",
        text, re.I,
    )
    if m:
        name = m.group("contact").strip()
        if name.lower() not in _STOP_WORDS:
            return name

    # Pattern 4: "<action> <NAME> on whatsapp"
    m = re.search(
        r"\b(?:send|message|text|msg)\s+"
        r"(?P<contact>[a-zA-Z]\w{1,20})"
        r"\s+(?:a\s+)?(?:message\s+)?(?:on|in|via)\s+(?:whatsapp|whats\s*app)\b",
        text, re.I,
    )
    if m:
        name = m.group("contact").strip()
        if name.lower() not in _STOP_WORDS:
            return name

    # Pattern 5: Fallback — "to <NAME>" anywhere in the text
    m = re.search(
        r"\bto\s+(?P<contact>[a-zA-Z]\w{1,20})\b",
        text, re.I,
    )
    if m:
        name = m.group("contact").strip()
        if name.lower() not in _STOP_WORDS:
            return name

    return None


def _extract_whatsapp_message(text: str) -> str | None:
    """Pull the message body from a WhatsApp command."""
    # "... saying hello" / "... say hi there" / "... that meet me at 5"
    m = re.search(
        r'\b(?:saying|say|and\s+say)\s+["\']?(?P<msg>.+?)["\']?\s*$',
        text, re.I,
    )
    return m.group("msg").strip() if m else None


def _enrich_whatsapp(text: str, params: dict[str, Any], hints: list[str]) -> str:
    """Resolve WhatsApp contact from phonebook and enrich params."""
    contact_name = _extract_whatsapp_contact(text)
    message_body = _extract_whatsapp_message(text)

    if contact_name:
        params["contact"] = contact_name
        hints.append(f"whatsapp_contact_extracted={contact_name}")

        # Resolve from phonebook
        try:
            from executor.whatsapp_phonebook import (
                _find_entry_by_keyword,
                _normalize_phone_queries,
                match_needles,
            )

            primary_key, entry = _find_entry_by_keyword(contact_name)
            if entry is not None:
                if isinstance(entry, str):
                    phone = entry
                    display = contact_name
                    aliases = []
                else:
                    phone = entry.get("phone", "")
                    display = entry.get("display_name", contact_name)
                    aliases = entry.get("aliases", [])

                params["phone_number"] = phone
                params["display_name"] = display
                params["contact_aliases"] = aliases
                params["search_strategy"] = "phone_number_first"
                params["search_queries"] = _normalize_phone_queries(phone)
                params["match_needles"] = match_needles(contact_name)
                hints.append(f"phonebook_resolved={display}→{phone}")
                logger.info(
                    "Enhancer: resolved %r → phone=%s display=%s",
                    contact_name,
                    phone,
                    display,
                )
            else:
                params["search_strategy"] = "name_only"
                hints.append("phonebook_miss")
                logger.info(
                    "Enhancer: contact %r not in phonebook — name-only search",
                    contact_name,
                )
        except ImportError:
            logger.debug("Phonebook module not available in enhancer")

    if message_body:
        params["message"] = message_body
        hints.append(f"whatsapp_message_extracted")

    return text


# ---------------------------------------------------------------------------
# Media / music enrichment
# ---------------------------------------------------------------------------


def _enrich_media(text: str, params: dict[str, Any], hints: list[str]) -> str:
    """Infer platform defaults for music/media commands."""
    # Check if platform is explicitly stated
    platform_match = _PLATFORM_RE.search(text)
    if platform_match:
        platform = platform_match.group(1).strip().lower()
        # Normalize
        if platform in ("yt music", "youtube music"):
            platform = "youtube_music"
        params["platform"] = platform
        hints.append(f"platform_explicit={platform}")
        return text

    # No explicit platform — apply defaults
    has_music_kw = _MUSIC_KEYWORDS_RE.search(text)
    has_play = _PLAY_RE.search(text)
    has_search = _SEARCH_RE.search(text)

    if has_search and has_music_kw:
        # "search for batman music" → YouTube
        params["platform"] = "youtube"
        hints.append("platform_default=youtube (music search)")

        # Extract the query (strip search/music keywords)
        query = re.sub(
            r"\b(search|find|look\s+up|browse)\s+(for\s+|about\s+)?",
            "",
            text,
            count=1,
            flags=re.I,
        ).strip()
        query = re.sub(r"\s*(music|song|track)\s*$", "", query, flags=re.I).strip()
        if query:
            params["query"] = query
            hints.append(f"media_query={query}")

    elif has_play:
        # "play batman theme" → default to YouTube (widely available)
        if not params.get("platform"):
            params["platform"] = "youtube"
            hints.append("platform_default=youtube (play command)")

    return text


# ---------------------------------------------------------------------------
# News / information enrichment
# ---------------------------------------------------------------------------


def _enrich_information(text: str, params: dict[str, Any], hints: list[str]) -> str:
    """Tag news/information commands and extract topics."""
    if _NEWS_RE.search(text):
        params["info_type"] = "news"
        hints.append("info_type=news")

        # Extract topic: "headlines about technology" → "technology"
        topic_match = re.search(
            r"\b(?:about|on|regarding|for)\s+(?P<topic>.+?)(?:\s*$|\s+(?:today|now|please))",
            text,
            re.I,
        )
        if topic_match:
            params["topic"] = topic_match.group("topic").strip()
            hints.append(f"news_topic={params['topic']}")
        else:
            params["topic"] = "latest news"

    elif _EXPLAIN_RE.search(text):
        params["info_type"] = "explain"
        hints.append("info_type=explain")

    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enhance_command(cleaned_input: str) -> EnhancedCommand:
    """
    Pre-classify and enrich a user command before it reaches the intent router.

    This is the main entry point called by ``node_enhance`` in the LangGraph.

    Parameters
    ----------
    cleaned_input : str
        The wake-word-stripped, normalized user input (from ``node_perceive``).

    Returns
    -------
    EnhancedCommand
        Contains the (possibly rewritten) input, pre-extracted params,
        broad category tag, and classifier hints.
    """
    if not cleaned_input or not cleaned_input.strip():
        return EnhancedCommand(enhanced_input=cleaned_input)

    text = cleaned_input.strip()
    params: dict[str, Any] = {}
    hints: list[str] = []

    # 1. Detect broad category
    category = _detect_category(text)
    hints.append(f"category={category}")

    # 2. Category-specific enrichment
    if category == "communication" and _WHATSAPP_RE.search(text):
        text = _enrich_whatsapp(text, params, hints)

    if category == "media":
        text = _enrich_media(text, params, hints)

    if category == "information":
        text = _enrich_information(text, params, hints)

    logger.info(
        "Enhanced: %r → category=%s, params=%s, hints=%s",
        cleaned_input,
        category,
        {k: v for k, v in params.items() if k != "match_needles"},  # needles are verbose
        hints,
    )

    return EnhancedCommand(
        enhanced_input=text,
        enhanced_params=params,
        category=category,
        hints=hints,
    )
