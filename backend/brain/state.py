from typing import Any, NotRequired, Optional, TypedDict, Annotated
import operator
from enum import Enum


class IntentCategory(str, Enum):
    MOUSE_MOVE       = "mouse_move"
    MOUSE_CLICK      = "mouse_click"
    MOUSE_SCROLL     = "mouse_scroll"
    MOUSE_DRAG       = "mouse_drag"
    KEYBOARD_TYPE    = "keyboard_type"
    KEYBOARD_HOTKEY  = "keyboard_hotkey"
    SCREEN_CAPTURE   = "screen_capture"
    SCREEN_READ      = "screen_read"
    WINDOW_OPEN      = "window_open"
    WINDOW_FOCUS     = "window_focus"
    WINDOW_CLOSE     = "window_close"
    CLIPBOARD_COPY   = "clipboard_copy"
    CLIPBOARD_PASTE  = "clipboard_paste"
    OPEN_APP         = "open_app"
    CLOSE_APP        = "close_app"
    SEARCH_WEB       = "search_web"
    OPEN_YOUTUBE     = "open_youtube"
    OPEN_WHATSAPP    = "open_whatsapp"
    TAB_CLEANUP      = "tab_cleanup"
    OPEN_FILE        = "open_file"
    VOLUME_SET       = "volume_set"
    VOLUME_MUTE      = "volume_mute"
    BRIGHTNESS       = "brightness"
    SYSTEM_INFO      = "system_info"
    SHUTDOWN         = "shutdown"
    RESTART          = "restart"
    SLEEP            = "sleep"
    LOCK             = "lock"
    PLAY_MEDIA       = "play_media"
    PAUSE_MEDIA      = "pause_media"
    NEXT_TRACK       = "next_track"
    PREV_TRACK       = "prev_track"
    CHAT             = "chat"
    SUMMARISE        = "summarise"
    EXPLAIN          = "explain"
    CALCULATE        = "calculate"
    TRANSLATE        = "translate"
    CODE_HELP        = "code_help"
    WRITE_TEXT       = "write_text"
    WEATHER          = "weather"
    NEWS             = "news"
    TIME_DATE        = "time_date"
    REMINDER         = "reminder"
    TIMER            = "timer"
    MULTI_STEP       = "multi_step"
    CLARIFY          = "clarify"
    UNKNOWN          = "unknown"


class ExecutionStatus(str, Enum):
    SUCCESS   = "success"
    FAILED    = "failed"
    PARTIAL   = "partial"
    PENDING   = "pending"
    SKIPPED   = "skipped"


class ToolCall(TypedDict):
    tool_name:  str
    parameters: dict[str, Any]
    result:     Optional[Any]
    status:     ExecutionStatus
    error:      Optional[str]


class AgentState(TypedDict):
    raw_input:          str
    session_id:         str
    timestamp:          float
    cleaned_input:      str
    screen_context:     Optional[str]
    active_window:      Optional[str]
    intent:             IntentCategory
    intent_confidence:  float
    extracted_params:   dict[str, Any]
    needs_clarification: bool
    clarification_prompt: Optional[str]
    plan:               list[str]
    tool_calls:         Annotated[list[ToolCall], operator.add]
    current_step:       int
    max_retries:        int
    short_term:         list[dict]
    retrieved_memories: list[str]
    user_preferences:   dict[str, Any]
    llm_provider:       str
    llm_model:          str
    llm_prompt:         Optional[str]
    llm_response:       Optional[str]
    final_response:     str
    tts_text:           str
    ui_event:           Optional[dict]
    execution_status:   ExecutionStatus
    route:              str
    error_message:      Optional[str]
    iteration_count:    int
    # --- Command enhancer extensions (command_enhancer.py) ---
    enhanced_input:         NotRequired[Optional[str]]
    enhanced_params:        NotRequired[dict[str, Any]]
    command_category:       NotRequired[Optional[str]]
    enhancer_hints:         NotRequired[list[str]]
    # --- Graph pipeline extensions (friday_graph.py) ---
    memory_context:         NotRequired[Optional[str]]
    classification_source:  NotRequired[str]   # "rule" | "llm"
    fast_path:              NotRequired[bool]
    needs_confirmation:     NotRequired[bool]
    confirmation_prompt:    NotRequired[Optional[str]]
    reflect_decision:       NotRequired[str]   # "continue" | "retry" | "finish"
