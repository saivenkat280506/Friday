"""
system.py — Volume, diagnostics, power, timer, and utility tool specs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "volume_set",
        "Set or adjust system volume.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
                "direction": {"type": "string", "enum": ["set", "up", "down"]},
            },
            "required": [],
        },
    ),
    _tool(
        "volume_mute",
        "Toggle system mute.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
    ),
    _tool(
        "system_info",
        "Report CPU, RAM, and disk usage.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
    ),
    _tool(
        "system_shutdown",
        "Schedule system shutdown.",
        ToolCategory.SYSTEM,
        SafetyLevel.HIGH,
        requires_confirmation=True,
    ),
    _tool(
        "system_restart",
        "Schedule system restart.",
        ToolCategory.SYSTEM,
        SafetyLevel.HIGH,
        requires_confirmation=True,
    ),
    _tool(
        "system_sleep",
        "Put the system to sleep.",
        ToolCategory.SYSTEM,
        SafetyLevel.HIGH,
        requires_confirmation=True,
    ),
    _tool(
        "system_lock",
        "Lock the workstation.",
        ToolCategory.SYSTEM,
        SafetyLevel.MEDIUM,
    ),
    _tool(
        "time_date",
        "Speak the current local time and date.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
        timeout_seconds=10,
    ),
    _tool(
        "timer_set",
        "Start a countdown timer with a desktop notification.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "minimum": 1},
                "unit": {"type": "string", "enum": ["second", "minute", "hour"]},
            },
            "required": ["duration"],
        },
    ),
    _tool(
        "calculate",
        "Evaluate a safe math expression.",
        ToolCategory.SYSTEM,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": [],
        },
        timeout_seconds=10,
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register all system tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)