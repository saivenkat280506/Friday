"""
communication.py — Messaging tool specs (metadata only).

WhatsApp delivery is implemented in ``whatsapp_handler.py`` and wired
through ``tool_handlers.AsyncToolHandlers.send_whatsapp_message``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "send_whatsapp_message",
        "Send a WhatsApp message to a named contact via WhatsApp Desktop.",
        ToolCategory.COMMUNICATION,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {
                "contact": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["contact", "message"],
        },
        timeout_seconds=75,
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register communication tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)