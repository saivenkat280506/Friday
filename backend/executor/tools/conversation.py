"""
conversation.py — Conversational pass-through tool spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "chat",
        "Conversational pass-through when no tool action is needed.",
        ToolCategory.CONVERSATION,
        SafetyLevel.LOW,
        timeout_seconds=5,
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register conversation tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)