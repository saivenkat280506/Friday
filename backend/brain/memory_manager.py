import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

logger = logging.getLogger("friday.memory")

DATA_DIR  = Path(os.getenv("FRIDAY_DATA_DIR", Path.home() / ".friday"))
PREFS_DIR = DATA_DIR / "preferences"
CHROMA_DIR = DATA_DIR / "chroma"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PREFS_DIR.mkdir(parents=True, exist_ok=True)


class MemoryManager:
    def __init__(self, short_term_limit: int = 20):
        self._short_term: dict[str, deque] = defaultdict(lambda: deque(maxlen=short_term_limit))
        self._chroma = None
        self._collection = None
        self._chroma_init_attempted = False

    def _ensure_chroma(self):
        if self._collection is not None or self._chroma_init_attempted:
            return
        self._chroma_init_attempted = True
        if os.getenv("FRIDAY_ENABLE_CHROMA", "").strip() not in ("1", "true", "yes"):
            return
        from brain.context_manager import is_resource_constrained
        if is_resource_constrained(ram_threshold=88.0):
            logger.info("ChromaDB init deferred — RAM constrained.")
            return
        self._init_chroma()

    def _init_chroma(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            self._chroma = client
            self._collection = client.get_or_create_collection(
                name="friday_long_term",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"ChromaDB ready. {self._collection.count()} memories stored.")
        except ImportError:
            logger.warning("chromadb not installed — long-term memory disabled.")
        except Exception as e:
            logger.warning(f"ChromaDB init failed: {e}")

    def get_short_term(self, session_id: str) -> list[dict]:
        return list(self._short_term[session_id])

    def add_exchange(self, session_id: str, user_text: str, assistant_text: str, intent: str = "", metadata: dict = None):
        entry = {"role": "user", "content": user_text, "timestamp": time.time(), "intent": intent}
        response_entry = {"role": "assistant", "content": assistant_text, "timestamp": time.time()}
        self._short_term[session_id].append(entry)
        self._short_term[session_id].append(response_entry)

        self._ensure_chroma()
        if len(user_text) > 10 and self._collection is not None:
            self._store_long_term(
                text=f"User: {user_text}\nFRIDAY: {assistant_text}",
                meta={"session_id": session_id, "intent": intent, "timestamp": str(time.time()), **(metadata or {})},
            )

    def _store_long_term(self, text: str, meta: dict = None):
        if self._collection is None:
            return
        try:
            doc_id = f"mem_{int(time.time() * 1000)}"
            self._collection.add(documents=[text], metadatas=[meta or {}], ids=[doc_id])
        except Exception as e:
            logger.debug(f"Long-term store error: {e}")

    def retrieve(self, query: str, k: int = 3) -> list[str]:
        # Never trigger Chroma download/embed on the request hot path
        if self._collection is None or not query.strip():
            return []
        try:
            results = self._collection.query(query_texts=[query], n_results=min(k, self._collection.count()))
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.debug(f"Memory retrieve error: {e}")
            return []

    def forget_session(self, session_id: str):
        self._short_term.pop(session_id, None)

    def clear_all_long_term(self):
        if self._collection:
            self._collection.delete(where={"session_id": {"$exists": True}})

    def get_preferences(self, session_id: str) -> dict:
        path = PREFS_DIR / f"{session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {"name": "Sai", "tone": "concise, friendly", "language": "en"}

    def set_preference(self, session_id: str, key: str, value: Any):
        prefs = self.get_preferences(session_id)
        prefs[key] = value
        (PREFS_DIR / f"{session_id}.json").write_text(json.dumps(prefs, indent=2))

    def update_preferences(self, session_id: str, updates: dict):
        prefs = self.get_preferences(session_id)
        prefs.update(updates)
        (PREFS_DIR / f"{session_id}.json").write_text(json.dumps(prefs, indent=2))
