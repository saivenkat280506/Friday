"""
safety.py — Guardrail Layer
============================
Ensures LLM output is valid and safe before execution.
"""

import json

ALLOWED_INTENTS = [
    "open_app", "send_whatsapp", "send_whatsapp_message", "play_music", "play_youtube", "play_youtube_music", "play_spotify_music",
    "search_browser", "search_and_browse", "cancel_task", "chat", "greeting",
    "capabilities", "news", "joke", "qa", "intro", "focus_window", "web_agent",
    "smart_search", "read_headlines", "mouse_scroll", "scroll_page", "move_mouse",
    "screenshot", "type_text", "click_at", "hotkey", "volume_control",
    "system_command", "search_news", "check_platform_messages",
    "os_control", "autonomous_task",
]

def validate_action(action_json: dict):
    """
    Checks if the LLM response is a valid tool call.
    Returns: (is_safe, validated_json)
    """
    if not isinstance(action_json, dict):
        return False, {"intent": "search_browser", "parameters": {}}
    
    intent = action_json.get("intent")
    
    if intent not in ALLOWED_INTENTS:
        # Fallback to search if intent is invalid
        return False, {
            "intent": "search_browser", 
            "parameters": {"query": "Default search fallback"}
        }
    
    # Basic parameter check
    if "parameters" not in action_json or not isinstance(action_json["parameters"], dict):
        return False, {
            "intent": "search_browser", 
            "parameters": {}
        }
        
    return True, action_json
