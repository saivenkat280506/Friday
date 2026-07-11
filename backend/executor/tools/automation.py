"""
automation.py — Reserved for future vision/OS automation async tools.

Sync automation tools (web_agent, search_and_browse, os_control) are defined
in ``skills_registry.json`` and handled by ``LegacySyncHandlers``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple = ()


def register(registry: ToolRegistry) -> None:
    """Register automation tool specs (none in async catalog yet)."""
    for spec in TOOLS:
        registry.register_spec(spec)