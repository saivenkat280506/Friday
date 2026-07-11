"""Central path constants for the FRIDAY project."""

from __future__ import annotations

import os

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
DATA_DIR = os.path.join(BACKEND_DIR, "data")

MEMORY_FILE = os.path.join(DATA_DIR, "friday_memory.json")
CHECKPOINTS_DB = os.path.join(DATA_DIR, "friday_checkpoints.db")
BROWSER_SESSIONS_DB = os.path.join(DATA_DIR, "browser_sessions.db")
MEMORY_STORE_DIR = os.path.join(DATA_DIR, "friday_memory_store")
WHATSAPP_RECORDINGS_DIR = os.path.join(DATA_DIR, "whatsapp-recordings")
WHATSAPP_PHONEBOOK_FILE = os.path.join(DATA_DIR, "whatsapp_phonebook.json")
WHATSAPP_SOLUTIONS_FILE = os.path.join(DATA_DIR, "whatsapp_solutions.json")


def ensure_data_dirs() -> None:
    """Create runtime data directories if they do not exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEMORY_STORE_DIR, exist_ok=True)
    os.makedirs(WHATSAPP_RECORDINGS_DIR, exist_ok=True)