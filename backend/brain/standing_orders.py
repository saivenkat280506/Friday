"""
standing_orders.py — Phase 5 Persistent Instructions
======================================================

Standing orders are rules the user has taught Friday that persist across sessions.

Examples:
  "Always confirm before sending WhatsApp"
  "Never open YouTube automatically"
  "From now on, play music on Spotify"
  "Always show me the weather when I ask"

Standing orders are:
  1. Stored in SQLite (lightweight, local)
  2. Injected into the LLM prompt context at perceive time
  3. Checked by permission_gate to bypass confirmation for specific tools

Doc §15: "WhatsApp send always confirm unless you create a standing order for one contact"

Usage:
    from brain.standing_orders import standing_orders, StandingOrder

    # Add a standing order
    standing_orders.add(StandingOrder(instruction="always confirm before sending WhatsApp"))

    # Check in permission gate
    standing_orders.grants_permission("send_whatsapp_message", {"contact": "Mum"})

    # Inject into context
    context = standing_orders.to_context_string()  # → inject into LLM prompt
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger("friday.standing_orders")

# ── Config ────────────────────────────────────────────────────────────────────

ORDERS_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "standing_orders.db"
MAX_ORDERS = 50

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class StandingOrder:
    instruction: str                # human-readable rule
    tool: str = ""                  # optional: specific tool this applies to
    contact: str = ""               # optional: specific contact (WhatsApp)
    grants_confirm: bool = False    # True = "skip confirmation for this tool+contact"
    blocks: bool = False            # True = "never do this"
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StandingOrder":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Text parsing ──────────────────────────────────────────────────────────────

_REMOVE_PATTERNS = [
    re.compile(r"\b(remove|cancel|forget|delete)\s+(standing\s+order|that\s+rule|the\s+rule)\b", re.I),
    re.compile(r"\bstop\s+always\b", re.I),
]

_GRANT_PATTERNS = [
    (re.compile(r"\balways\s+confirm\b", re.I), "confirm"),
    (re.compile(r"\bstop\s+asking\s+me\b", re.I), "no_confirm"),
    (re.compile(r"\bnever\s+ask\b", re.I), "no_confirm"),
    (re.compile(r"\balways\s+(?:use|play\s+on|open\s+on)\s+(\w+)\b", re.I), "platform"),
]

_TOOL_KEYWORDS: dict[str, str] = {
    "whatsapp": "send_whatsapp_message",
    "email": "send_email",
    "delete": "delete_file",
    "music": "play_music",
    "spotify": "play_music",
    "open_app": "open_app",
}


def _parse_standing_order(text: str) -> tuple[str, StandingOrder | None]:
    """
    Parse user text into (action, StandingOrder|None).
    action: "add" | "remove"
    """
    text_lower = text.lower().strip()

    # Check remove intent
    for p in _REMOVE_PATTERNS:
        if p.search(text_lower):
            return "remove", None

    # Detect target tool
    tool = ""
    for kw, tool_name in _TOOL_KEYWORDS.items():
        if kw in text_lower:
            tool = tool_name
            break

    # Detect contact (for WhatsApp)
    contact = ""
    m = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z\s]{1,25}?)(?:\s+always|\s+never|\s*$)", text, re.I)
    if m:
        contact = m.group(1).strip()

    # Detect grant or block
    grants_confirm = False
    blocks = False
    if re.search(r"\balways\s+confirm\b", text_lower):
        # e.g. "always confirm before sending WhatsApp"
        # → keep requires_confirm behaviour (default)
        pass  # grants_confirm stays False — confirmation STAYS
    if re.search(r"\b(stop\s+asking|never\s+ask|auto\s+approve|no\s+confirm)\b", text_lower):
        grants_confirm = True  # skip confirm for this tool/contact
    if re.search(r"\bnever\b", text_lower) and not re.search(r"\bnever\s+ask\b", text_lower):
        blocks = True

    instruction = text.strip()

    return "add", StandingOrder(
        instruction=instruction,
        tool=tool,
        contact=contact,
        grants_confirm=grants_confirm,
        blocks=blocks,
    )


# ── Store ─────────────────────────────────────────────────────────────────────


class StandingOrderStore:
    """
    Thread-safe, SQLite-backed standing orders store.
    """

    def __init__(self, db_path: Path = ORDERS_DB_PATH) -> None:
        self._lock = threading.RLock()
        self._orders: list[StandingOrder] = []
        self._db_path = db_path
        self._db_ready = False
        self._init_db()
        self._load()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self._db_path))
            con.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            con.commit()
            con.close()
            self._db_ready = True
        except Exception as exc:
            logger.warning("[StandingOrders] DB init failed (in-memory): %s", exc)

    def _load(self) -> None:
        if not self._db_ready:
            return
        try:
            con = sqlite3.connect(str(self._db_path))
            rows = con.execute("SELECT data FROM orders ORDER BY created_at").fetchall()
            con.close()
            with self._lock:
                for (data_json,) in rows:
                    try:
                        self._orders.append(StandingOrder.from_dict(json.loads(data_json)))
                    except Exception:
                        pass
            logger.info("[StandingOrders] Loaded %d order(s)", len(self._orders))
        except Exception as exc:
            logger.warning("[StandingOrders] DB load failed: %s", exc)

    def _save(self, order: StandingOrder) -> None:
        if not self._db_ready:
            return
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute(
                "INSERT OR REPLACE INTO orders (id, data, created_at) VALUES (?,?,?)",
                (order.id, json.dumps(order.to_dict()), order.created_at),
            )
            con.commit()
            con.close()
        except Exception as exc:
            logger.debug("[StandingOrders] save error: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, order: StandingOrder) -> str:
        with self._lock:
            if len(self._orders) >= MAX_ORDERS:
                self._orders.pop(0)
            self._orders.append(order)
        self._save(order)
        logger.info("[StandingOrders] Added: %r (tool=%s)", order.instruction[:60], order.tool)
        return order.id

    def remove_by_id(self, order_id: str) -> bool:
        with self._lock:
            before = len(self._orders)
            self._orders = [o for o in self._orders if o.id != order_id]
            removed = len(self._orders) < before
        if removed and self._db_ready:
            try:
                con = sqlite3.connect(str(self._db_path))
                con.execute("DELETE FROM orders WHERE id=?", (order_id,))
                con.commit()
                con.close()
            except Exception:
                pass
        return removed

    def remove_by_text(self, text: str) -> int:
        """Remove orders whose instruction is similar to text."""
        norm = text.lower().strip()
        to_remove = []
        with self._lock:
            for o in self._orders:
                if norm[:30] in o.instruction.lower() or o.instruction.lower()[:30] in norm:
                    to_remove.append(o.id)
        count = 0
        for oid in to_remove:
            if self.remove_by_id(oid):
                count += 1
        return count

    def grants_permission(self, tool: str, params: dict) -> bool:
        """Return True if a standing order explicitly grants permission for this tool+params."""
        contact = str(params.get("contact") or "").lower()
        with self._lock:
            for o in self._orders:
                if not o.grants_confirm:
                    continue
                if o.tool and o.tool != tool:
                    continue
                if o.contact and o.contact.lower() not in contact and contact not in o.contact.lower():
                    continue
                return True
        return False

    def blocks_tool(self, tool: str, params: dict) -> bool:
        """Return True if a standing order explicitly blocks this tool."""
        with self._lock:
            for o in self._orders:
                if not o.blocks:
                    continue
                if o.tool and o.tool == tool:
                    return True
        return False

    def all_orders(self) -> list[StandingOrder]:
        with self._lock:
            return list(self._orders)

    def to_context_string(self) -> str:
        """
        Compact string injected into the LLM prompt context.
        Max ~200 chars to stay token-efficient.
        """
        with self._lock:
            orders = list(self._orders)
        if not orders:
            return ""
        lines = [f"- {o.instruction}" for o in orders[:10]]
        return "Standing orders:\n" + "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._orders.clear()
        if self._db_ready:
            try:
                con = sqlite3.connect(str(self._db_path))
                con.execute("DELETE FROM orders")
                con.commit()
                con.close()
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────────────────────────

standing_orders = StandingOrderStore()
