"""
tools_registry.py — FRIDAY Tool Registry (MCP-inspired)

Separates *tool definitions* (LLM-facing metadata) from *handlers*
(``tool_handlers.py``). The registry owns catalog, lookup, and dispatch.

Async tool specs live in modular category files under ``executor/tools/``.
Sync / Groq-path tools remain in ``backend/brain/skills_registry.json``.

To add a new async tool:
1. Add a ``Tool`` spec in the appropriate ``executor/tools/<category>.py`` file.
2. Register the module in ``executor/tools/__init__.py`` if new.
3. Implement the handler in ``tool_handlers.AsyncToolHandlers``.

To add a new sync tool:
1. Add an entry to ``skills_registry.json``.
2. Implement the handler in ``tool_handlers.LegacySyncHandlers``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from executor.tool_handlers import (
    AsyncHandler,
    AsyncToolHandlers,
    LegacySyncHandlers,
    SyncHandler,
    build_async_handler_map,
)

if TYPE_CHECKING:
    from brain.state import AgentState, ExecutionStatus, IntentCategory, ToolCall

logger = logging.getLogger("friday.tools")

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_RETRY_COUNT = 0

_LEGACY_SAFETY_MAP: dict[str, str] = {
    "safe": "low",
    "moderate": "medium",
    "destructive": "high",
    "critical": "high",
    "low": "low",
    "medium": "medium",
    "high": "high",
}

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ToolResult: TypeAlias = dict[str, Any]
ExecutionKind = Literal["async", "sync"]
SafetyTier = Literal["low", "medium", "high"]


class SafetyLevel(str, Enum):
    """Normalized risk tier (low / medium / high) for planners and safety checks."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCategory(str, Enum):
    """High-level grouping for LLM tool routing."""

    APPLICATION = "application"
    AUTOMATION = "automation"
    COMMUNICATION = "communication"
    CONVERSATION = "conversation"
    INPUT = "input"
    MEDIA = "media"
    SYSTEM = "system"
    VISION = "vision"
    WEB = "web"


def _empty_parameters() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


def normalize_safety_level(value: str | SafetyLevel) -> SafetyLevel:
    """Map legacy safety strings (safe/moderate/…) to low/medium/high."""
    raw = value.value if isinstance(value, SafetyLevel) else str(value).lower()
    normalized = _LEGACY_SAFETY_MAP.get(raw, "low")
    return SafetyLevel(normalized)


class Tool(BaseModel):
    """
    MCP-style tool metadata.

    ``parameters`` follows JSON Schema ``object`` shape (type/properties/required).
    Handlers are bound separately in ``ToolRegistry`` so definitions stay serializable.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=_empty_parameters)
    category: ToolCategory
    safety_level: SafetyLevel
    requires_confirmation: bool = False
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retry_count: int = DEFAULT_RETRY_COUNT
    aliases: tuple[str, ...] = ()
    execution: ExecutionKind = "async"

    def to_mcp_schema(self) -> dict[str, Any]:
        """Export a compact schema suitable for LLM tool-calling prompts."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "safety_level": self.safety_level.value,
            "requires_confirmation": self.requires_confirmation,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "parameters": self.parameters,
            "aliases": list(self.aliases),
            "execution": self.execution,
        }


# Backward-compatible alias used by older imports and ``register()`` signatures.
ToolDefinition = Tool


def _tool(
    name: str,
    description: str,
    category: ToolCategory,
    safety: SafetyLevel,
    parameters: dict[str, Any] | None = None,
    *,
    requires_confirmation: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_count: int = DEFAULT_RETRY_COUNT,
    aliases: tuple[str, ...] = (),
) -> Tool:
    """Shorthand factory for async tool specs."""
    return Tool(
        name=name,
        description=description,
        category=category,
        safety_level=safety,
        parameters=parameters or _empty_parameters(),
        requires_confirmation=requires_confirmation,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        aliases=aliases,
        execution="async",
    )


# ---------------------------------------------------------------------------
# Async tool catalog — LangGraph / ToolRegistry.execute_tool() path
# Specs are defined in executor/tools/<category>.py modules.
# ---------------------------------------------------------------------------


def get_async_tool_specs() -> dict[str, Tool]:
    """Return the async tool catalog from modular category files."""
    from executor.tools import collect_async_tools

    return collect_async_tools()


# Backward-compatible module-level alias (lazy on first access).
ASYNC_TOOL_SPECS: dict[str, Tool] | None = None


def _async_tool_specs() -> dict[str, Tool]:
    global ASYNC_TOOL_SPECS
    if ASYNC_TOOL_SPECS is None:
        ASYNC_TOOL_SPECS = get_async_tool_specs()
    return ASYNC_TOOL_SPECS


# ---------------------------------------------------------------------------
# Skills registry I/O
# ---------------------------------------------------------------------------


def _state_module():
    """Load ``brain.state`` without importing ``brain.__init__`` (breaks cycles)."""
    import importlib.util

    module_name = "brain.state"
    if module_name in sys.modules:
        return sys.modules[module_name]

    state_path = Path(__file__).resolve().parent.parent / "brain" / "state.py"
    spec = importlib.util.spec_from_file_location(module_name, state_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load state module from {state_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_skills_registry(path: Path | None = None) -> list[dict[str, Any]]:
    """
    Load LLM-facing tool definitions from ``skills_registry.json``.

    Returns the ``tools`` array from the v3 schema, or an empty list on failure.
    """
    registry_path = path or Path(__file__).resolve().parent.parent / "brain" / "skills_registry.json"
    try:
        with registry_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        tools = payload.get("tools", payload if isinstance(payload, list) else [])
        return tools if isinstance(tools, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load skills registry at %s: %s", registry_path, exc)
        return []


def _skills_entry_to_tool(entry: dict[str, Any]) -> Tool:
    """Convert a ``skills_registry.json`` entry into a ``Tool``."""
    category_raw = entry.get("category", "system")
    try:
        category = ToolCategory(category_raw)
    except ValueError:
        category = ToolCategory.SYSTEM

    safety = normalize_safety_level(entry.get("safety_level", "low"))

    return Tool(
        name=entry["name"],
        description=entry.get("description", ""),
        category=category,
        safety_level=safety,
        requires_confirmation=bool(entry.get("requires_confirmation", False)),
        parameters=entry.get("parameters", _empty_parameters()),
        timeout_seconds=int(entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        retry_count=int(entry.get("retry_count", DEFAULT_RETRY_COUNT)),
        aliases=tuple(entry.get("aliases", [])),
        execution="sync",
    )


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """
    Central registry for FRIDAY tools.

    Public API:
    - ``get_tool()`` — resolve sync handler (legacy executor path)
    - ``list_tools()`` — all ``Tool`` metadata records
    - ``get_tools_for_llm()`` — MCP-style schemas for prompts
    - ``execute_tool()`` — async dispatch with timeout/retry (LangGraph path)

    Legacy aliases preserved: ``execute``, ``get_definition``, ``get_tool_schemas``,
    ``get_simple_plan``, ``list_tool_names``, ``register``.
    """

    def __init__(self) -> None:
        self._async_handlers = AsyncToolHandlers()
        self._tools: dict[str, Tool] = {}
        self._alias_index: dict[str, str] = {}
        self._async_map: dict[str, AsyncHandler] = {}
        self._sync_handlers: dict[str, SyncHandler] = LegacySyncHandlers.build()

        from executor.tools import register_all

        register_all(self)
        async_names = [name for name, spec in self._tools.items() if spec.execution == "async"]
        self._async_map = build_async_handler_map(self._async_handlers, async_names)
        self._index_sync_tools_from_skills()

    def _index_sync_tools_from_skills(self) -> None:
        """Hydrate sync tool metadata from ``skills_registry.json``."""
        for entry in load_skills_registry():
            if not entry.get("name"):
                continue
            tool = _skills_entry_to_tool(entry)
            self._tools[tool.name] = tool
            self._alias_index[tool.name] = tool.name
            for alias in tool.aliases:
                self._alias_index[alias] = tool.name

    def _resolve_name(self, name: str) -> str:
        return self._alias_index.get(name, name)

    def get_tool(self, name: str) -> SyncHandler | None:
        """Return the sync handler for an intent name (``tool_executor`` path)."""
        resolved = self._resolve_name(name)
        return self._sync_handlers.get(resolved) or self._sync_handlers.get(name)

    def list_tools(self, *, include_aliases: bool = False) -> list[Tool]:
        """Return all registered tool definitions (async + sync catalog)."""
        tools = list(self._tools.values())
        if include_aliases:
            return tools
        seen: set[str] = set()
        unique: list[Tool] = []
        for tool in tools:
            if tool.name in seen:
                continue
            seen.add(tool.name)
            unique.append(tool)
        return unique

    def register_spec(self, tool: Tool) -> None:
        """Register async tool metadata from a category module."""
        self._tools[tool.name] = tool
        self._alias_index[tool.name] = tool.name
        for alias in tool.aliases:
            self._alias_index[alias] = tool.name

    def get_tools_for_llm(self, *, include_sync: bool = True) -> list[dict[str, Any]]:
        """Return MCP-style schemas for LLM tool selection prompts."""
        async_specs = _async_tool_specs()
        schemas = [spec.to_mcp_schema() for spec in async_specs.values()]
        if not include_sync:
            return schemas
        seen: set[str] = {spec.name for spec in async_specs.values()}
        for entry in load_skills_registry():
            name = entry.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            schemas.append(_skills_entry_to_tool(entry).to_mcp_schema())
        return schemas

    def get_tool_schemas(self, *, include_sync: bool = True) -> list[dict[str, Any]]:
        """Backward-compatible alias for ``get_tools_for_llm``."""
        return self.get_tools_for_llm(include_sync=include_sync)

    def get_definition(self, tool_name: str) -> Tool | None:
        """Return metadata for a tool by canonical name or alias."""
        resolved = self._resolve_name(tool_name)
        return self._tools.get(resolved) or self._tools.get(tool_name)

    def register(
        self,
        definition: Tool,
        handler: AsyncHandler,
        *,
        aliases: list[str] | None = None,
    ) -> None:
        """Register a new async tool at runtime."""
        self._tools[definition.name] = definition
        self._async_map[definition.name] = handler
        self._alias_index[definition.name] = definition.name
        for alias in aliases or list(definition.aliases):
            self._async_map[alias] = handler
            self._alias_index[alias] = definition.name

    def get_simple_plan(self, intent: IntentCategory, params: dict[str, Any]) -> list[str] | None:
        """Map a classified intent to a single-step tool plan string."""
        state = _state_module()
        IntentCategory = state.IntentCategory
        simple: dict[IntentCategory, str] = {
            IntentCategory.MOUSE_CLICK: f"mouse_click:{params.get('location', '')}",
            IntentCategory.MOUSE_SCROLL: (
                f"mouse_scroll:{params.get('direction', 'down')}:{params.get('amount', 3)}"
            ),
            IntentCategory.KEYBOARD_TYPE: f"keyboard_type:{params.get('text', '')}",
            IntentCategory.KEYBOARD_HOTKEY: f"keyboard_hotkey:{','.join(params.get('keys', []))}",
            IntentCategory.SCREEN_CAPTURE: "screen_capture:",
            IntentCategory.SCREEN_READ: "screen_read:",
            IntentCategory.OPEN_APP: f"open_app:{params.get('app', '')}",
            IntentCategory.WINDOW_FOCUS: f"window_focus:{params.get('app', '')}",
            IntentCategory.WINDOW_CLOSE: f"window_close:{params.get('app', '')}",
            IntentCategory.CLIPBOARD_COPY: "clipboard_copy:",
            IntentCategory.CLIPBOARD_PASTE: "clipboard_paste:",
            IntentCategory.VOLUME_SET: (
                f"volume_set:{params.get('level', '')}:{params.get('direction', 'set')}"
            ),
            IntentCategory.VOLUME_MUTE: "volume_mute:",
            IntentCategory.SYSTEM_INFO: "system_info:",
            IntentCategory.TIME_DATE: "time_date:",
            IntentCategory.NEWS: f"read_headlines:{params.get('query') or 'latest news'}",
            IntentCategory.SEARCH_WEB: f"search_web:{params.get('query', '')}",
            IntentCategory.OPEN_YOUTUBE: f"open_youtube:{params.get('query', '')}",
            IntentCategory.OPEN_WHATSAPP: f"send_whatsapp_message:{params.get('contact', '')}",
            IntentCategory.TAB_CLEANUP: "smart_tab_cleanup:",
            IntentCategory.PLAY_MEDIA: (
                f"play_music:{params.get('song', '')}:{params.get('platform', 'spotify')}"
                if params.get("song") or params.get("platform")
                else "media_play:"
            ),
            IntentCategory.PAUSE_MEDIA: "media_pause:",
            IntentCategory.NEXT_TRACK: "media_next:",
            IntentCategory.PREV_TRACK: "media_prev:",
            IntentCategory.SHUTDOWN: "system_shutdown:",
            IntentCategory.RESTART: "system_restart:",
            IntentCategory.SLEEP: "system_sleep:",
            IntentCategory.LOCK: "system_lock:",
            IntentCategory.TIMER: (
                f"timer_set:{params.get('duration', 1)}:{params.get('unit', 'minute')}"
            ),
        }
        val = simple.get(intent)
        return [val] if val is not None else None

    def list_tool_names(self) -> list[str]:
        """List registered async tool names (used by the LangGraph planner)."""
        return list(self._async_map.keys())

    async def execute_tool(
        self,
        tool_name: str,
        raw_param: str,
        params: dict[str, Any],
        state: AgentState,
    ) -> ToolCall:
        """
        Execute an async tool with timeout and optional retries.

        Wraps the handler outcome in a ``ToolCall`` record for the graph pipeline.
        """
        state_mod = _state_module()
        ToolCall = state_mod.ToolCall
        ExecutionStatus = state_mod.ExecutionStatus

        resolved = self._resolve_name(tool_name)
        fn = self._async_map.get(resolved) or self._async_map.get(tool_name)
        if not fn:
            return ToolCall(
                tool_name=tool_name,
                parameters={"raw_param": raw_param},
                result=None,
                status=ExecutionStatus.FAILED,
                error=f"Unknown tool: {tool_name}",
            )

        definition = self.get_definition(resolved) or self.get_definition(tool_name)
        timeout = definition.timeout_seconds if definition else DEFAULT_TIMEOUT_SECONDS
        retries = definition.retry_count if definition else DEFAULT_RETRY_COUNT
        merged = {**params, "_raw": raw_param}

        last_error: str | None = None
        attempts = 1 + max(0, retries)
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(
                    fn(raw_param, merged, state),
                    timeout=timeout,
                )
                status = (
                    ExecutionStatus.SUCCESS
                    if result.get("status") == "success"
                    else ExecutionStatus.FAILED
                )
                return ToolCall(
                    tool_name=tool_name,
                    parameters=merged,
                    result=result,
                    status=status,
                    error=result.get("error"),
                )
            except asyncio.TimeoutError:
                last_error = f"Tool '{tool_name}' timed out after {timeout}s"
                logger.warning("%s (attempt %d/%d)", last_error, attempt + 1, attempts)
            except Exception as exc:
                last_error = str(exc)
                logger.exception("Tool %s raised on attempt %d: %s", tool_name, attempt + 1, exc)

        return ToolCall(
            tool_name=tool_name,
            parameters=merged,
            result=None,
            status=ExecutionStatus.FAILED,
            error=last_error or f"Tool '{tool_name}' failed",
        )

    async def execute(
        self,
        tool_name: str,
        raw_param: str,
        params: dict[str, Any],
        state: AgentState,
    ) -> ToolCall:
        """Backward-compatible alias for ``execute_tool``."""
        return await self.execute_tool(tool_name, raw_param, params, state)


_registry_instance: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Return the process-wide singleton ``ToolRegistry``."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ToolRegistry()
    return _registry_instance


# Backward-compatible module-level sync map consumed by ``tool_executor.get_tool()``.
TOOL_MAP: dict[str, SyncHandler] = LegacySyncHandlers.build()


def get_tool(intent: str) -> SyncHandler | None:
    """Resolve a sync tool handler by intent name (legacy executor path)."""
    return TOOL_MAP.get(intent)
