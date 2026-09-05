"""
responses.py — Concise Voice Responses
=====================================
"""

RESPONSES = {
    "open_app_start": "Will do, Boss.",
    "send_whatsapp_start": "Working on it, Boss.",
    "play_music_start": "As you wish.",
    "search_start": "Looking that up, Boss.",
    "general_start": "On it, Boss.",
    "open_app_success": "Done, Boss.",
    "open_app_fail": "I couldn't open that, Boss.",
    "send_whatsapp_success": "Sent, Boss.",
    "send_whatsapp_fail": "That didn't go through, Boss.",
    "play_music_success": "Playing now, Boss.",
    "play_music_fail": "I couldn't find that track, Boss.",
    "search_success": "Query complete, Boss.",
    "search_fail": "Nothing useful came back, Boss.",
    "unknown": "I don't have a protocol for that, Boss.",
    "standby": "At your service, Boss.",
}

def get_response(key: str):
    """Returns a short, natural response."""
    return RESPONSES.get(key, "Processed.")

