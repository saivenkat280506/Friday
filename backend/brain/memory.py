"""
memory.py — Simple Short-Term Memory
====================================
Stores recent interactions to provide context.
"""

import json
import os
import threading

from paths import MEMORY_FILE, ensure_data_dirs

ensure_data_dirs()
_lock = threading.Lock()

def _load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"history": [], "last_contact": None, "last_song": None}

def save_memory(key, value):
    with _lock:
        mem = _load_memory()
        mem[key] = value
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f)

def get_memory(key):
    with _lock:
        mem = _load_memory()
        return mem.get(key)

def add_to_history(command):
    with _lock:
        mem = _load_memory()
        history = mem.get("history", [])
        history.append(command)
        if len(history) > 5:
            history = history[-5:]
        mem["history"] = history
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f)
