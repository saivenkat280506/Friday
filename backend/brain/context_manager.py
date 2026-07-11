"""
context_manager.py — Contextual Intelligence
============================================
Combines current input with memory plus live system metrics to create
a rich context string for the LLM.
"""

import ctypes
import time
from datetime import datetime
from brain.memory import get_memory
from brain.memory_store import get_memory_store

# ── Lightweight resource guard (cached ~5s) ───────────────────────────────────
_resource_cache: dict = {"ts": 0.0, "constrained": False, "ram_pct": 0.0, "cpu_pct": 0.0}


def is_resource_constrained(ram_threshold: float = 82.0) -> bool:
    """True when RAM is high — skip heavy local models to avoid OOM stalls."""
    now = time.time()
    if now - _resource_cache["ts"] < 5.0:
        return _resource_cache["constrained"]
    try:
        import psutil
        mem = psutil.virtual_memory().percent
        cpu = psutil.cpu_percent(interval=None)
        constrained = mem >= ram_threshold
        _resource_cache.update(ts=now, constrained=constrained, ram_pct=mem, cpu_pct=cpu)
    except Exception:
        _resource_cache.update(ts=now, constrained=False, ram_pct=0.0, cpu_pct=0.0)
    return _resource_cache["constrained"]


def get_resource_snapshot() -> dict:
    """Cached RAM/CPU snapshot for monitors and throttlers."""
    is_resource_constrained()
    return {
        "ram_percent": _resource_cache["ram_pct"],
        "cpu_percent": _resource_cache["cpu_pct"],
        "constrained": _resource_cache["constrained"],
    }

# ── System metrics collectors ──────────────────────────────────────────────────

def _get_active_window_title() -> str:
    """Returns the title of the currently focused window (Windows only)."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
        return buf.value
    except Exception:
        return ""

def _get_battery_status() -> str:
    """Returns battery percentage and charging status."""
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return ""
        pct = int(batt.percent)
        status = "charging" if batt.power_plugged else "discharging"
        return f"Battery at {pct} percent, {status}"
    except Exception:
        return ""

def _get_system_load() -> str:
    """Returns brief CPU and memory load."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        return f"CPU at {cpu} percent, memory at {mem} percent"
    except Exception:
        return ""

def build_system_context() -> str:
    """
    Collects live metrics: time, battery, active window, system load.
    Returns a plain-English string for injection into LLM context.
    """
    now = datetime.now()
    time_str = now.strftime("%A, %B %d, %I:%M %p")
    tod = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"

    parts = [f"Current local time: {time_str} ({tod})"]

    win = _get_active_window_title()
    if win:
        parts.append(f"Active window: {win}")

    batt = _get_battery_status()
    if batt:
        parts.append(batt)

    load = _get_system_load()
    if load:
        parts.append(load)

    return " | ".join(parts)


# ── Pronoun resolution ─────────────────────────────────────────────────────────

def resolve_pronouns(text: str):
    """
    Directly resolves pronouns in the user input using memory.
    Returns the resolved text (for display) and resolved params (for action).
    """
    import re
    text_lower = text.lower()
    resolved_params = {}
    
    if any(re.search(rf"\b{re.escape(k)}\b", text_lower) for k in ["it", "again", "that song", "the same"]):
        last_song = get_memory("last_song")
        if last_song:
            resolved_params["song"] = last_song
            text = re.sub(r"\bit\b", f"'{last_song}'", text, flags=re.IGNORECASE)
            text = re.sub(r"\bagain\b", f"'{last_song}' again", text, flags=re.IGNORECASE)
    
    if any(re.search(rf"\b{re.escape(k)}\b", text_lower) for k in ["him", "her", "that person"]):
        last_contact = get_memory("last_contact")
        if last_contact:
            resolved_params["contact"] = last_contact
            text = re.sub(r"\bhim\b", f"'{last_contact}'", text, flags=re.IGNORECASE)
            text = re.sub(r"\bher\b", f"'{last_contact}'", text, flags=re.IGNORECASE)
    
    return text, resolved_params


def get_current_context():
    """
    Builds a structured summary of recent activity plus live system metrics.
    Now enriched with ChromaDB memory if available.
    """
    system = build_system_context()
    history = get_memory("history") or []
    last_contact = get_memory("last_contact")
    last_song = get_memory("last_song")
    
    context_parts = [system]
    if history:
        recent = history[-3:]
        context_parts.append(f"Recent activity: {', '.join(recent)}")
    if last_contact:
        context_parts.append(f"Last contacted person: {last_contact}")
    if last_song:
        context_parts.append(f"Last played song or artist: {last_song}")
    
    # Enrich with ChromaDB memory if available
    store = get_memory_store()
    if store.is_ready:
        prefs = store.get_all_preferences()
        if prefs:
            pref_str = "; ".join(f"{k}: {v}" for k, v in prefs.items())
            context_parts.append(f"Known preferences: {pref_str}")
        
        recent_exchanges = store.format_recent_for_prompt(n=3)
        if recent_exchanges:
            context_parts.append(f"Recent context:\n{recent_exchanges}")
        
    return " | ".join(context_parts)


def get_enriched_context(query_text: str = "") -> str:
    """
    Build context enriched with semantically relevant memory for a given query.
    Used for more targeted LLM prompt injection when the query is known.
    """
    system = build_system_context()
    store = get_memory_store()
    
    parts = [system]
    
    if store.is_ready and query_text:
        memory_context = store.format_context_for_prompt(query_text)
        if memory_context:
            parts.append(memory_context)
    
    return "\n\n".join(parts)
