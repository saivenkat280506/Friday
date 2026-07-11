import json
import os
import threading

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
_lock = threading.Lock()

DEFAULTS = {
    "muted": False,
    "autoWake": True,
    "realTimeFeedback": True,
    "volume": 80,
    "confidence": 85,
    "theme": "light",
    "persona": "witty, concise, tactical",
}

def get_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)

def save_settings(settings: dict):
    merged = {**DEFAULTS, **settings}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(merged, f, indent=2)

def update_settings(patch: dict) -> dict:
    with _lock:
        current = get_settings()
        current.update(patch)
        save_settings(current)
        return current

def is_muted() -> bool:
    return get_settings().get("muted", False)

def toggle_mute() -> bool:
    with _lock:
        settings = get_settings()
        settings["muted"] = not settings.get("muted", False)
        save_settings(settings)
        return settings["muted"]
