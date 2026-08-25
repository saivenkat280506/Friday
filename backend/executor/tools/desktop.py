"""
desktop.py — Input, clipboard, application, and window tool specifications.

Handlers: ``AsyncToolHandlers`` methods matching each tool name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools._common import SafetyLevel, Tool, ToolCategory, _tool

if TYPE_CHECKING:
    from executor.tools_registry import ToolRegistry

TOOLS: tuple[Tool, ...] = (
    _tool(
        "mouse_click",
        "Click at pixel coordinates or a named on-screen location.",
        ToolCategory.INPUT,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "location": {"type": "string"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
            },
            "required": [],
        },
    ),
    _tool(
        "mouse_move",
        "Move the mouse cursor to pixel coordinates.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
    ),
    _tool(
        "mouse_scroll",
        "Scroll the active window up or down.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "minimum": 1},
            },
            "required": [],
        },
    ),
    _tool(
        "mouse_drag",
        "Drag from start coordinates to end coordinates.",
        ToolCategory.INPUT,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {
                "sx": {"type": "integer"},
                "sy": {"type": "integer"},
                "ex": {"type": "integer"},
                "ey": {"type": "integer"},
            },
            "required": ["sx", "sy", "ex", "ey"],
        },
    ),
    _tool(
        "keyboard_type",
        "Type text at the focused input.",
        ToolCategory.INPUT,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    _tool(
        "keyboard_hotkey",
        "Press a keyboard shortcut.",
        ToolCategory.INPUT,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
        },
    ),
    _tool(
        "keyboard_press",
        "Press a single key.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    ),
    _tool(
        "clipboard_copy",
        "Copy text to the clipboard.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": [],
        },
    ),
    _tool(
        "clipboard_paste",
        "Paste clipboard contents at the cursor.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
    ),
    _tool(
        "clipboard_read",
        "Read the current clipboard text.",
        ToolCategory.INPUT,
        SafetyLevel.LOW,
    ),
    _tool(
        "open_app",
        "Launch an installed application.",
        ToolCategory.APPLICATION,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
    ),
    _tool(
        "open_vscode_new_project",
        "Open Visual Studio Code in a new window with a fresh empty project folder (not an existing workspace).",
        ToolCategory.APPLICATION,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Optional folder name for the new project",
                }
            },
            "required": [],
        },
    ),
    _tool(
        "window_focus",
        "Bring an application window to the foreground.",
        ToolCategory.APPLICATION,
        SafetyLevel.LOW,
        {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
    ),
    _tool(
        "window_close",
        "Close an application window.",
        ToolCategory.APPLICATION,
        SafetyLevel.MEDIUM,
        {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
    ),
)


def register(registry: ToolRegistry) -> None:
    """Register all desktop/input/application tool specs."""
    for spec in TOOLS:
        registry.register_spec(spec)