"""
executor.tools — Modular FRIDAY tool catalog registration.

Each category module exposes:
- ``TOOLS`` — tuple of ``Tool`` metadata records
- ``register(registry)`` — register specs with a ``ToolRegistry`` instance

To add a new async tool:
1. Add the spec to the appropriate category file (or create a new module).
2. Implement the handler in ``tool_handlers.AsyncToolHandlers``.
3. Import the module in ``_TOOL_MODULES`` below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from executor.tools import (
    automation,
    communication,
    conversation,
    desktop,
    media,
    system,
    vision,
    web,
)

if TYPE_CHECKING:
    from executor.tools_registry import Tool, ToolRegistry

_TOOL_MODULES = (
    web,
    desktop,
    vision,
    system,
    media,
    communication,
    conversation,
    automation,
)


def collect_async_tools() -> dict[str, Tool]:
    """Build the async tool catalog from all category modules."""
    from executor.tools_registry import Tool

    catalog: dict[str, Tool] = {}
    for module in _TOOL_MODULES:
        for spec in module.TOOLS:
            catalog[spec.name] = spec
    return catalog


def register_all(registry: ToolRegistry) -> None:
    """Register every category module with the central registry."""
    for module in _TOOL_MODULES:
        module.register(registry)


__all__ = [
    "automation",
    "communication",
    "conversation",
    "desktop",
    "media",
    "system",
    "vision",
    "web",
    "collect_async_tools",
    "register_all",
]