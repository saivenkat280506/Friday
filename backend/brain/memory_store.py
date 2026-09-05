"""
memory_store.py — ChromaDB Memory Layer
=========================================
Persistent, semantic-searchable memory for FRIDAY.
Three collections: episodic, preference, conversation context.
Embedding via all-MiniLM-L6-v2 for CPU-friendly semantic search.
"""

import json
import os
import time
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_CHROMA_AVAILABLE = None
_EMBEDDING_AVAILABLE = None


def _check_chroma():
    global _CHROMA_AVAILABLE
    if _CHROMA_AVAILABLE is None:
        try:
            import chromadb
            _CHROMA_AVAILABLE = True
        except ImportError:
            _CHROMA_AVAILABLE = False
            logger.warning("chromadb not installed. MemoryStore will be disabled.")
    return _CHROMA_AVAILABLE


def _check_embedding():
    global _EMBEDDING_AVAILABLE
    if _EMBEDDING_AVAILABLE is None:
        try:
            import sentence_transformers  # noqa: F401 — import only, do not load weights
            _EMBEDDING_AVAILABLE = True
        except Exception:
            _EMBEDDING_AVAILABLE = False
            logger.warning("sentence-transformers not available. MemoryStore disabled.")
    return _EMBEDDING_AVAILABLE


# ── Default persist directory (relative to backend) ─────────────────────────────
from paths import MEMORY_STORE_DIR as _DEFAULT_PERSIST_DIR

# ── Collection names ────────────────────────────────────────────────────────────
EPISODIC_COLLECTION = "episodic_memory"
PREFERENCE_COLLECTION = "preference_memory"
CONVERSATION_COLLECTION = "conversation_context"

# ── Limits ──────────────────────────────────────────────────────────────────────
MAX_CONVERSATIONS = 100


class MemoryStore:
    """
    Persistent, semantically-searchable memory for FRIDAY.
    
    Three collections:
      - episodic_memory: every completed task with intent, params, result, timestamp
      - preference_memory: user preferences as key-value pairs with confidence scores
      - conversation_context: recent role/content exchanges with embeddings
    
    Usage:
        store = get_memory_store()
        store.store_episode(intent="search_browser", params={"query": "AI"}, result="success")
        results = store.query_episodes("what did I search for earlier")
    """

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or _DEFAULT_PERSIST_DIR
        self._client = None
        self._collections = {}
        self._embedding_fn = None
        self._ready = False
        self._init_failed = False

    def _ensure_ready(self) -> bool:
        if self._ready:
            return True
        if self._init_failed:
            return False
        # Chroma downloads a 79MB model and freezes the server on first use.
        # Enable only when explicitly opted in: FRIDAY_ENABLE_CHROMA=1
        if os.getenv("FRIDAY_ENABLE_CHROMA", "").strip() not in ("1", "true", "yes"):
            self._init_failed = True
            return False
        from brain.context_manager import is_resource_constrained
        if is_resource_constrained(ram_threshold=88.0):
            logger.info("MemoryStore init deferred — RAM constrained.")
            return False
        self._setup()
        if not self._ready:
            self._init_failed = True
        return self._ready

    def _setup(self):
        if not _check_chroma() or not _check_embedding():
            logger.warning("MemoryStore unavailable — chromadb or sentence-transformers missing.")
            return

        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            os.makedirs(self._persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._persist_dir)
            self._embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )

            for name in [EPISODIC_COLLECTION, PREFERENCE_COLLECTION, CONVERSATION_COLLECTION]:
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    embedding_function=self._embedding_fn,
                )

            self._ready = True
            logger.info(f"MemoryStore ready at {self._persist_dir}")

        except Exception as e:
            logger.warning(f"MemoryStore init failed: {e}")
            self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ensure_ready()

    # ── Episodic Memory ──────────────────────────────────────────────────────────

    def store_episode(
        self,
        intent: str,
        params: dict,
        result: str,
        response_text: str = "",
        active_window: str = "",
    ):
        """Store a completed task execution episode."""
        if not self._ensure_ready():
            return

        try:
            ts = time.time()
            dt_str = datetime.fromtimestamp(ts).isoformat()
            episode_id = f"ep_{int(ts * 1000)}_{int(time.time() * 1000) % 10000}"

            # Build a searchable description
            param_str = ", ".join(f"{k}={v}" for k, v in (params or {}).items())
            desc_parts = [f"Task: {intent}"]
            if param_str:
                desc_parts.append(f"Parameters: {param_str}")
            desc_parts.append(f"Result: {result}")
            if response_text:
                desc_parts.append(f"Response: {response_text}")
            if active_window:
                desc_parts.append(f"Window: {active_window}")
            description = ". ".join(desc_parts)

            self._collections[EPISODIC_COLLECTION].add(
                ids=[episode_id],
                documents=[description],
                metadatas=[{
                    "intent": intent,
                    "params": json.dumps(params or {}),
                    "result": str(result)[:200],
                    "response_text": str(response_text)[:200],
                    "active_window": active_window,
                    "timestamp": dt_str,
                    "timestamp_epoch": ts,
                }],
            )
        except Exception as e:
            logger.warning(f"store_episode failed: {e}")

    def query_episodes(self, query_text: str, n_results: int = 5) -> list[dict]:
        """Find episodes semantically similar to query_text."""
        if not self._ensure_ready():
            return []
        try:
            results = self._collections[EPISODIC_COLLECTION].query(
                query_texts=[query_text],
                n_results=n_results,
            )
            return self._format_results(results)
        except Exception as e:
            logger.warning(f"query_episodes failed: {e}")
            return []

    def get_recent_episodes(self, n: int = 5) -> list[dict]:
        """Get the most recent episodes by timestamp."""
        if not self._ensure_ready():
            return []
        try:
            results = self._collections[EPISODIC_COLLECTION].get(limit=n)
            return self._format_get_results(results)
        except Exception as e:
            logger.warning(f"get_recent_episodes failed: {e}")
            return []

    def store_browser_episode(
        self,
        task: str,
        actions: str,
        outcome: str,
        dom_summary: str = "",
    ):
        """Store a completed browser automation episode for episodic RAG."""
        self.store_episode(
            intent="browser_agent",
            params={"task": task, "dom_summary": dom_summary, "actions": actions[:2000]},
            result=outcome,
            response_text=dom_summary,
            active_window="browser",
        )

    # ── Preference Memory ────────────────────────────────────────────────────────

    def store_preference(self, key: str, value: str, confidence: float = 1.0):
        """Store or update a user preference."""
        if not self._ensure_ready():
            return

        try:
            doc_text = f"User preference: {key} = {value}"
            metadata = {
                "key": key,
                "value": str(value)[:500],
                "confidence": confidence,
                "updated_at": datetime.now().isoformat(),
            }

            existing = self._collections[PREFERENCE_COLLECTION].get(ids=[key])
            if existing and existing.get("ids"):
                self._collections[PREFERENCE_COLLECTION].update(
                    ids=[key],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
            else:
                self._collections[PREFERENCE_COLLECTION].add(
                    ids=[key],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
        except Exception as e:
            logger.warning(f"store_preference failed: {e}")

    def get_preference(self, key: str) -> Optional[dict]:
        """Get a specific preference by key."""
        if not self._ensure_ready():
            return None
        try:
            results = self._collections[PREFERENCE_COLLECTION].get(ids=[key])
            if results and results.get("ids"):
                return self._format_get_results(results)[0]
            return None
        except Exception:
            return None

    def get_all_preferences(self) -> dict:
        """Get all stored preferences as a flat dict for context injection."""
        if not self._ensure_ready():
            return {}
        try:
            results = self._collections[PREFERENCE_COLLECTION].get()
            metadatas = results.get("metadatas", []) or []
            flat = {}
            for m in metadatas:
                if m and "key" in m:
                    flat[m["key"]] = m.get("value", "")
            return flat
        except Exception:
            return {}

    def query_preferences(self, query_text: str, n_results: int = 3) -> list[dict]:
        """Find semantically relevant preferences."""
        if not self._ensure_ready():
            return []
        try:
            results = self._collections[PREFERENCE_COLLECTION].query(
                query_texts=[query_text],
                n_results=n_results,
            )
            return self._format_results(results)
        except Exception as e:
            logger.warning(f"query_preferences failed: {e}")
            return []

    # ── Conversation Context ─────────────────────────────────────────────────────

    def store_exchange(self, role: str, content: str):
        """Store a single exchange (user or assistant) in conversation context."""
        if not self._ensure_ready() or not content:
            return

        try:
            ts = time.time()
            dt_str = datetime.fromtimestamp(ts).isoformat()
            exchange_id = f"conv_{int(ts * 1000)}_{role[:4]}"

            self._collections[CONVERSATION_COLLECTION].add(
                ids=[exchange_id],
                documents=[content],
                metadatas=[{
                    "role": role,
                    "timestamp": dt_str,
                    "timestamp_epoch": ts,
                }],
            )

            # Enforce max conversation limit — remove oldest if exceeding
            count = self._collections[CONVERSATION_COLLECTION].count()
            if count > MAX_CONVERSATIONS:
                all_results = self._collections[CONVERSATION_COLLECTION].get(
                    limit=count,
                    offset=0,
                )
                ids = all_results.get("ids", [])
                if len(ids) > MAX_CONVERSATIONS:
                    ids_to_remove = ids[: len(ids) - MAX_CONVERSATIONS]
                    self._collections[CONVERSATION_COLLECTION].delete(ids=ids_to_remove)
        except Exception as e:
            logger.warning(f"store_exchange failed: {e}")

    def query_context(self, query_text: str, n_results: int = 3) -> list[dict]:
        """Find most semantically relevant past exchanges."""
        if not self._ensure_ready():
            return []
        try:
            results = self._collections[CONVERSATION_COLLECTION].query(
                query_texts=[query_text],
                n_results=n_results,
            )
            return self._format_results(results)
        except Exception as e:
            logger.warning(f"query_context failed: {e}")
            return []

    def get_recent_exchanges(self, n: int = 10) -> list[dict]:
        """Get recent conversation exchanges for prompt injection."""
        if not self._ensure_ready():
            return []
        try:
            count = self._collections[CONVERSATION_COLLECTION].count()
            results = self._collections[CONVERSATION_COLLECTION].get(
                limit=min(n, count),
                offset=max(0, count - n),
            )
            return self._format_get_results(results)
        except Exception as e:
            logger.warning(f"get_recent_exchanges failed: {e}")
            return []

    def format_recent_for_prompt(self, n: int = 5) -> str:
        """Format recent exchanges as a string for LLM prompt injection."""
        exchanges = self.get_recent_exchanges(n)
        if not exchanges:
            return ""
        lines = []
        for ex in exchanges:
            role = ex.get("metadata", {}).get("role", "user")
            doc = ex.get("document", "")
            if role == "user":
                lines.append(f"User: {doc}")
            else:
                lines.append(f"Assistant: {doc}")
        return "\n".join(lines)

    def format_context_for_prompt(self, query_text: str = "") -> str:
        """
        Build a comprehensive context string from all memory sources.
        Used for injection into LLM system prompts.
        """
        parts = []

        # Relevant past episodes
        if query_text:
            episodes = self.query_episodes(query_text, n_results=3)
            if episodes:
                ep_lines = []
                for ep in episodes:
                    doc = ep.get("document", "")
                    if doc:
                        ep_lines.append(f"- {doc}")
                if ep_lines:
                    parts.append("Recent relevant tasks:\n" + "\n".join(ep_lines))

        # Preferences
        prefs = self.get_all_preferences()
        if prefs:
            pref_lines = [f"{k}: {v}" for k, v in prefs.items()]
            parts.append("Known preferences: " + "; ".join(pref_lines))

        # Recent conversation context
        recent = self.format_recent_for_prompt(n=5)
        if recent:
            parts.append("Recent conversation:\n" + recent)

        return "\n\n".join(parts)

    # ── Helpers ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_results(results: dict) -> list[dict]:
        """Format ChromaDB query results into a clean list of dicts."""
        formatted = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i in range(len(ids)):
            formatted.append({
                "id": ids[i],
                "document": documents[i] if documents and i < len(documents) else "",
                "metadata": metadatas[i] if metadatas and i < len(metadatas) else {},
                "distance": distances[i] if distances and i < len(distances) else None,
            })
        return formatted

    @staticmethod
    def _format_get_results(results: dict) -> list[dict]:
        """Format ChromaDB get results into a clean list of dicts."""
        formatted = []
        ids = results.get("ids", [])
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])

        for i in range(len(ids)):
            formatted.append({
                "id": ids[i],
                "document": documents[i] if documents and i < len(documents) else "",
                "metadata": metadatas[i] if metadatas and i < len(metadatas) else {},
            })
        return formatted

    def summarize_and_compress_episodes(self, age_days: int = 1):
        """
        Retrieve episodes older than age_days, use Groq to summarize them
        into a single high-level summary paragraph, store the summary in ChromaDB preferences,
        and delete the detailed old episodes to save memory.
        """
        if not self._ensure_ready():
            return
        
        try:
            import time
            import json
            from config import settings
            import httpx
            
            now_epoch = time.time()
            cutoff_epoch = now_epoch - (age_days * 86400)
            
            # Fetch all episodes to filter locally
            results = self._collections[EPISODIC_COLLECTION].get()
            ids = results.get("ids", [])
            metadatas = results.get("metadatas", [])
            documents = results.get("documents", [])
            
            old_ids = []
            old_episodes_desc = []
            
            for i in range(len(ids)):
                meta = metadatas[i]
                if meta and meta.get("timestamp_epoch", 0) < cutoff_epoch:
                    old_ids.append(ids[i])
                    old_episodes_desc.append(documents[i])
                    
            if not old_ids:
                print("[MemoryStore] No old episodes to consolidate.")
                return
                
            print(f"[MemoryStore] Consolidating {len(old_ids)} old episodes...")
            
            combined_log = "\n".join(f"- {desc}" for desc in old_episodes_desc)
            summarize_system = (
                __import__(
                    "brain.friday_persona", fromlist=["build_summarize_prompt"]
                ).build_summarize_prompt()
                + " Dense narrative paragraph of key takeaways and habits."
            )
            from config import use_ollama
            summary_text = ""
            if use_ollama():
                from brain.ollama_client import get_ollama
                summary_text = get_ollama().complete_sync(
                    combined_log, system=summarize_system, max_tokens=200, temperature=0.3
                )
            elif settings.GROQ_API_KEY:
                payload = {
                    "model": "openai/gpt-oss-20b",
                    "messages": [
                        {"role": "system", "content": summarize_system},
                        {"role": "user", "content": combined_log},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 200,
                }
                headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
                with httpx.Client(timeout=15.0) as client:
                    r = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                    r.raise_for_status()
                    summary_text = r.json()["choices"][0]["message"]["content"].strip()
            if not summary_text:
                summary_text = f"Consolidated log of {len(old_ids)} interactions."
                
            # Store the consolidated summary in preference memory as 'long_term_summary'
            self.store_preference("long_term_summary", summary_text, confidence=1.0)
            
            # Delete the detailed old episodes from episodic collection
            self._collections[EPISODIC_COLLECTION].delete(ids=old_ids)
            print(f"[MemoryStore] Successfully consolidated and deleted {len(old_ids)} detailed episodes.")
            
        except Exception as e:
            logger.warning(f"summarize_and_compress_episodes failed: {e}")



# ── Singleton ────────────────────────────────────────────────────────────────────
_store_instance = None


def get_memory_store(persist_dir: Optional[str] = None) -> MemoryStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = MemoryStore(persist_dir=persist_dir)
    return _store_instance
