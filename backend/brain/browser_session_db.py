"""
browser_session_db.py — SQLite log for browser automation sessions and actions.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import aiosqlite

from paths import BROWSER_SESSIONS_DB, ensure_data_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_sessions (
  id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  mode TEXT NOT NULL,
  started_at REAL NOT NULL,
  ended_at REAL,
  status TEXT NOT NULL,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS browser_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  action TEXT NOT NULL,
  selector TEXT,
  result TEXT,
  dom_url TEXT,
  timestamp REAL NOT NULL,
  FOREIGN KEY(session_id) REFERENCES browser_sessions(id)
);
"""


class BrowserSessionDB:
    def __init__(self, db_path: str = BROWSER_SESSIONS_DB):
        self.db_path = db_path
        self._ready = False

    async def _ensure(self) -> None:
        if self._ready:
            return
        ensure_data_dirs()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._ready = True

    async def start_session(self, task: str, mode: str) -> str:
        await self._ensure()
        session_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO browser_sessions (id, task, mode, started_at, status) VALUES (?, ?, ?, ?, ?)",
                (session_id, task, mode, time.time(), "running"),
            )
            await db.commit()
        return session_id

    async def log_action(
        self,
        session_id: str,
        step: int,
        action: str,
        *,
        selector: str | None = None,
        result: str | None = None,
        dom_url: str | None = None,
    ) -> None:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO browser_actions
                   (session_id, step, action, selector, result, dom_url, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, step, action, selector, result, dom_url, time.time()),
            )
            await db.commit()

    async def end_session(self, session_id: str, status: str, summary: str) -> None:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE browser_sessions SET ended_at=?, status=?, summary=? WHERE id=?",
                (time.time(), status, summary, session_id),
            )
            await db.commit()

    async def get_session_actions(self, session_id: str) -> list[dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT step, action, selector, result, dom_url, timestamp FROM browser_actions "
                "WHERE session_id=? ORDER BY step",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


_db: BrowserSessionDB | None = None


def get_browser_session_db() -> BrowserSessionDB:
    global _db
    if _db is None:
        _db = BrowserSessionDB()
    return _db


def format_actions_for_memory(actions: list[dict[str, Any]]) -> str:
    return json.dumps(actions, ensure_ascii=False)[:4000]