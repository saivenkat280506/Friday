"""
web.py — Web and browser tool specifications.

Handlers: ``AsyncToolHandlers.search_web``, ``AsyncToolHandlers.open_youtube``
Sync handlers: ``LegacySyncHandlers`` (search_browser, search_and_browse, etc. in skills_registry.json)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "search_web",
        "Open a Google search in the default browser.",
        ToolCategory.WEB,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    _tool(
        "open_youtube",
        "Open YouTube with an optional search query.",
        ToolCategory.WEB,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": [],
        },
    ),
    _tool(
        "browser_agent",
        "Run DOM-based human-like browser automation for search, navigation, and media.",
        ToolCategory.WEB,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
        },
        timeout_seconds=120,
    ),
    _tool(
        "smart_tab_cleanup",
        "Close browser tabs that aren't needed (duplicates, blank tabs, old searches, or matching specific keywords).",
        ToolCategory.WEB,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "close_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords of tab titles to target for closing."
                },
                "keep_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords of tab titles to protect from closing."
                }
            },
            "required": [],
        },
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register all web-related async tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)