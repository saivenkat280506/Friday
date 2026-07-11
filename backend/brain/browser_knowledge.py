"""
browser_knowledge.py — LlamaIndex RAG for browser recipes and episodic memory.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("friday.browser_knowledge")

_LLAMA_INDEX_AVAILABLE: bool | None = None

SITE_RECIPES: list[dict[str, str]] = [
    {
        "site": "google",
        "task": "search",
        "steps": "Open google.com, type in textarea[name='q'], press Enter",
        "selectors": "textarea[name='q'], input[name='q']",
    },
    {
        "site": "spotify",
        "task": "play music",
        "steps": "Open open.spotify.com/search/{song}, click first track row, click play button",
        "selectors": "[data-testid='track-row'], [data-testid='play-button']",
    },
    {
        "site": "youtube",
        "task": "play video",
        "steps": "Search on youtube.com/results?search_query=, click first video, press k or play button",
        "selectors": "ytd-video-renderer a#video-title, .ytp-play-button",
    },
    {
        "site": "spotify",
        "task": "media control",
        "steps": "Use control-button-play/pause/skip-forward/skip-back data-testid buttons",
        "selectors": "[data-testid='control-button-play'], [data-testid='control-button-pause']",
    },
]


def _check_llama_index() -> bool:
    global _LLAMA_INDEX_AVAILABLE
    if _LLAMA_INDEX_AVAILABLE is None:
        try:
            import llama_index  # noqa: F401
            _LLAMA_INDEX_AVAILABLE = True
        except ImportError:
            _LLAMA_INDEX_AVAILABLE = False
            logger.info("llama-index not installed — browser RAG uses static recipes")
    return _LLAMA_INDEX_AVAILABLE


class BrowserKnowledge:
    """Retrieve browser automation context for LangChain agent prompts."""

    def __init__(self):
        self._index = None
        self._ready = False

    def _build_static_context(self, query: str) -> str:
        q = query.lower()
        hits = []
        for doc in SITE_RECIPES:
            blob = f"{doc['site']} {doc['task']} {doc['steps']} {doc['selectors']}".lower()
            if any(token in blob for token in q.split() if len(token) > 2):
                hits.append(
                    f"[{doc['site']}] {doc['task']}: {doc['steps']} | selectors: {doc['selectors']}"
                )
        if not hits:
            hits = [
                f"[{d['site']}] {d['task']}: {d['selectors']}" for d in SITE_RECIPES[:3]
            ]
        return "\n".join(hits[:5])

    def _try_init_index(self) -> bool:
        if self._ready:
            return self._index is not None
        self._ready = True
        if not _check_llama_index():
            return False
        try:
            from llama_index.core import Document, VectorStoreIndex
            from llama_index.core import Settings as LISettings
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding
            from llama_index.vector_stores.chroma import ChromaVectorStore
            import chromadb
            from paths import MEMORY_STORE_DIR

            LISettings.embed_model = HuggingFaceEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
            client = chromadb.PersistentClient(path=MEMORY_STORE_DIR)
            collection = client.get_or_create_collection("browser_knowledge")
            vector_store = ChromaVectorStore(chroma_collection=collection)
            docs = [
                Document(
                    text=f"{r['site']} {r['task']}: {r['steps']} selectors={r['selectors']}",
                    metadata=r,
                )
                for r in SITE_RECIPES
            ]
            self._index = VectorStoreIndex.from_documents(docs, vector_store=vector_store)
            return True
        except Exception as exc:
            logger.warning("BrowserKnowledge index init failed: %s", exc)
            self._index = None
            return False

    def retrieve(self, query: str, *, top_k: int = 4) -> str:
        if self._try_init_index() and self._index is not None:
            try:
                retriever = self._index.as_retriever(similarity_top_k=top_k)
                nodes = retriever.retrieve(query)
                if nodes:
                    return "\n".join(n.get_content() for n in nodes)
            except Exception as exc:
                logger.debug("LlamaIndex retrieve failed: %s", exc)
        return self._build_static_context(query)

    def retrieve_episodic(self, query: str) -> str:
        try:
            from brain.memory_store import get_memory_store
            store = get_memory_store()
            if store.is_ready:
                episodes = store.query_episodes(query, n_results=3)
                if episodes:
                    return "\n".join(
                        ep.get("document", "") for ep in episodes if ep.get("document")
                    )
        except Exception as exc:
            logger.debug("Episodic retrieve failed: %s", exc)
        return ""

    def build_agent_context(self, task: str, page_state: dict[str, Any] | None = None) -> str:
        url = (page_state or {}).get("url", "")
        platform = ((page_state or {}).get("media") or {}).get("platform", "")
        query = f"task: {task} | url: {url} | platform: {platform}"
        recipe_ctx = self.retrieve(query)
        episode_ctx = self.retrieve_episodic(task)
        parts = ["## Site recipes", recipe_ctx]
        if episode_ctx:
            parts.extend(["## Similar past tasks", episode_ctx])
        return "\n".join(parts)


_knowledge: BrowserKnowledge | None = None


def get_browser_knowledge() -> BrowserKnowledge:
    global _knowledge
    if _knowledge is None:
        _knowledge = BrowserKnowledge()
    return _knowledge