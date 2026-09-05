"""
agenda.py — Phase 3 Agenda / Goal Stack
=========================================

Friday's personal goal stack with SQLite persistence.

Doc §13 Phase 3:
  - Agenda stack with goals
  - Initiative allowlist: build failed, reminder due, goal completed
  - Rate limit: max 1 unsolicited spoken line per N minutes unless urgent

Each Goal has:
  - A description (what Friday will say/do when triggered)
  - A trigger type: "time" | "app" | "keyword" | "manual"
  - A trigger value matching the trigger type
  - An expiry (optional)
  - A fired flag

Usage:
    from brain.agenda import agenda, Goal, TriggerType
    agenda.add_goal(Goal(description="Remind you about standup", trigger="time", trigger_value="09:00"))
    pending = agenda.get_pending_goals(world_snapshot)
    for goal in pending:
        agenda.mark_fired(goal.id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from perception.world import WorldSnapshot

logger = logging.getLogger("friday.agenda")

# ── Config ────────────────────────────────────────────────────────────────────

AGENDA_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "agenda.db"
MAX_GOALS = 20          # maximum concurrent goals
FIRE_COOLDOWN_S = 30    # don't re-fire same goal within 30s

# ── Data model ────────────────────────────────────────────────────────────────


class TriggerType:
    TIME    = "time"       # trigger_value: "HH:MM" (daily) or ISO timestamp
    APP     = "app"        # trigger_value: app display name substring, e.g. "Calendar"
    KEYWORD = "keyword"    # trigger_value: word(s) to match in window title
    MANUAL  = "manual"     # only fired explicitly via mark_fired by user/graph
    ONCE    = "once"       # fire once immediately (next inner-loop tick)


@dataclass
class Goal:
    description: str                    # what Friday will say/do
    trigger: str = TriggerType.ONCE     # trigger type
    trigger_value: str = ""             # trigger condition value
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None     # None = never
    fired: bool = False
    urgent: bool = False                # bypasses rate limit
    last_fired_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Trigger evaluation ────────────────────────────────────────────────────────


def _time_trigger_matches(trigger_value: str) -> bool:
    """
    trigger_value formats:
      "HH:MM"         — daily at this time (±1 min window)
      ISO timestamp   — fire once at this exact time
    """
    now = datetime.now()
    # Try HH:MM daily
    if len(trigger_value) == 5 and trigger_value[2] == ":":
        try:
            h, m = int(trigger_value[:2]), int(trigger_value[3:])
            return now.hour == h and now.minute == m
        except ValueError:
            pass
    # Try ISO timestamp (once)
    try:
        target = datetime.fromisoformat(trigger_value)
        now_cmp = datetime.now(target.tzinfo) if target.tzinfo is not None else now
        return abs((now_cmp - target).total_seconds()) < 60
    except Exception:
        pass
    return False


def _app_trigger_matches(trigger_value: str, world: "WorldSnapshot") -> bool:
    if not trigger_value:
        return False
    tv = trigger_value.lower()
    return (
        tv in world.app_display.lower()
        or tv in world.app.lower()
        or tv in world.window_title.lower()
    )


def _keyword_trigger_matches(trigger_value: str, world: "WorldSnapshot") -> bool:
    if not trigger_value:
        return False
    keywords = [k.strip().lower() for k in trigger_value.split(",") if k.strip()]
    title_lower = world.window_title.lower()
    return any(kw in title_lower for kw in keywords)


def _goal_is_triggered(goal: Goal, world: "WorldSnapshot | None") -> bool:
    if goal.fired:
        return False
    if goal.expires_at and time.time() > goal.expires_at:
        return False
    # Don't re-fire within cooldown
    if time.time() - goal.last_fired_at < FIRE_COOLDOWN_S:
        return False

    t = goal.trigger
    tv = goal.trigger_value

    if t == TriggerType.ONCE:
        return True
    if t == TriggerType.TIME:
        return _time_trigger_matches(tv)
    if t == TriggerType.MANUAL:
        return False  # only fired explicitly
    if world is None:
        return False
    if t == TriggerType.APP:
        return _app_trigger_matches(tv, world)
    if t == TriggerType.KEYWORD:
        return _keyword_trigger_matches(tv, world)
    return False


# ── Agenda state ─────────────────────────────────────────────────────────────


class AgendaStore:
    """
    Thread-safe goal stack with optional SQLite persistence.
    """

    def __init__(self, db_path: Path = AGENDA_DB_PATH) -> None:
        self._lock = threading.RLock()
        self._goals: list[Goal] = []
        self._db_path = db_path
        self._db_ready = False
        self._init_db()
        self._load_from_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self._db_path))
            con.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0
                )
            """)
            con.commit()
            con.close()
            self._db_ready = True
        except Exception as exc:
            logger.warning("[Agenda] DB init failed (in-memory only): %s", exc)

    def _load_from_db(self) -> None:
        if not self._db_ready:
            return
        try:
            con = sqlite3.connect(str(self._db_path))
            rows = con.execute(
                "SELECT data FROM goals WHERE fired = 0 ORDER BY created_at"
            ).fetchall()
            con.close()
            with self._lock:
                for (data_json,) in rows:
                    try:
                        self._goals.append(Goal.from_dict(json.loads(data_json)))
                    except Exception:
                        pass
            logger.info("[Agenda] Loaded %d pending goal(s) from DB", len(self._goals))
        except Exception as exc:
            logger.warning("[Agenda] DB load failed: %s", exc)

    def _save_goal_db(self, goal: Goal) -> None:
        if not self._db_ready:
            return
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute(
                "INSERT OR REPLACE INTO goals (id, data, created_at, fired) VALUES (?,?,?,?)",
                (goal.id, json.dumps(goal.to_dict()), goal.created_at, int(goal.fired)),
            )
            con.commit()
            con.close()
        except Exception as exc:
            logger.debug("[Agenda] DB save error: %s", exc)

    def _update_goal_db(self, goal: Goal) -> None:
        if not self._db_ready:
            return
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute(
                "UPDATE goals SET data=?, fired=? WHERE id=?",
                (json.dumps(goal.to_dict()), int(goal.fired), goal.id),
            )
            con.commit()
            con.close()
        except Exception as exc:
            logger.debug("[Agenda] DB update error: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_goal(self, goal: Goal) -> str:
        """Add a goal. Returns goal.id. Enforces MAX_GOALS limit."""
        with self._lock:
            # Prune expired/fired goals first
            self._goals = [g for g in self._goals if not g.fired]
            if len(self._goals) >= MAX_GOALS:
                # Remove oldest non-urgent goal
                non_urgent = [i for i, g in enumerate(self._goals) if not g.urgent]
                if non_urgent:
                    self._goals.pop(non_urgent[0])
            self._goals.append(goal)
        self._save_goal_db(goal)
        logger.info("[Agenda] Goal added: %r (trigger=%s)", goal.description[:60], goal.trigger)
        return goal.id

    def remove_goal(self, goal_id: str) -> bool:
        """Remove a goal by ID. Returns True if found."""
        with self._lock:
            before = len(self._goals)
            self._goals = [g for g in self._goals if g.id != goal_id]
            removed = len(self._goals) < before
        if removed:
            if self._db_ready:
                try:
                    con = sqlite3.connect(str(self._db_path))
                    con.execute("DELETE FROM goals WHERE id=?", (goal_id,))
                    con.commit()
                    con.close()
                except Exception:
                    pass
        return removed

    def mark_fired(self, goal_id: str) -> None:
        with self._lock:
            matched_once = False
            for g in self._goals:
                if g.id == goal_id:
                    g.fired = True
                    g.last_fired_at = time.time()
                    self._update_goal_db(g)
                    matched_once = (g.trigger == TriggerType.ONCE)
                    break
            if matched_once:
                self._goals = [x for x in self._goals if x.id != goal_id]

    def get_pending_goals(self, world: "WorldSnapshot | None" = None) -> list[Goal]:
        """Return goals whose trigger condition is currently met."""
        with self._lock:
            snap = list(self._goals)
        return [g for g in snap if _goal_is_triggered(g, world)]

    def all_goals(self) -> list[Goal]:
        with self._lock:
            return list(self._goals)

    def clear(self) -> None:
        with self._lock:
            self._goals.clear()
        if self._db_ready:
            try:
                con = sqlite3.connect(str(self._db_path))
                con.execute("DELETE FROM goals")
                con.commit()
                con.close()
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────────────────────────

agenda = AgendaStore()
