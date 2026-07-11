"""
vision.py — Screen capture, OCR, and on-screen element lookup tool specs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "screen_capture",
        "Capture a screenshot and save it to Pictures.",
        ToolCategory.VISION,
        SafetyLevel.LOW,
    ),
    _tool(
        "screen_read",
        "OCR-read text from the active screen or window.",
        ToolCategory.VISION,
        SafetyLevel.LOW,
    ),
    _tool(
        "find_on_screen",
        "Locate a UI element on screen by name.",
        ToolCategory.VISION,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register all vision-related tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)