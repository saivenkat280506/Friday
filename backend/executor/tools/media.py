"""
media.py — Media playback control tool specs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "media_play",
        "Resume or toggle media playback.",
        ToolCategory.MEDIA,
        SafetyLevel.LOW,
    ),
    _tool(
        "play_music",
        "Play a song on a streaming platform.",
        ToolCategory.MEDIA,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "song": {"type": "string"},
                "platform": {"type": "string", "enum": ["spotify", "youtube", "youtube_music"]},
            },
            "required": [],
        },
        timeout_seconds=60,
    ),
    _tool(
        "media_pause",
        "Pause media playback.",
        ToolCategory.MEDIA,
        SafetyLevel.LOW,
    ),
    _tool(
        "media_next",
        "Skip to the next track.",
        ToolCategory.MEDIA,
        SafetyLevel.LOW,
    ),
    _tool(
        "media_prev",
        "Go to the previous track.",
        ToolCategory.MEDIA,
        SafetyLevel.LOW,
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register all media tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)