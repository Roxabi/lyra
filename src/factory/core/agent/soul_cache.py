"""In-memory write-through cache for soul.md (sync load — no blob I/O in core)."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

__all__ = ["SoulCacheEntry", "SoulDocumentCache", "get_soul_document_cache"]


@dataclass
class SoulCacheEntry:
    blob_ref: str | None
    markdown: str
    composed_prompt: str


class SoulDocumentCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._by_agent: dict[str, SoulCacheEntry] = {}

    def put(
        self,
        agent_name: str,
        *,
        blob_ref: str | None,
        markdown: str,
        composed_prompt: str,
    ) -> None:
        with self._lock:
            self._by_agent[agent_name] = SoulCacheEntry(
                blob_ref=blob_ref,
                markdown=markdown,
                composed_prompt=composed_prompt,
            )

    def get(self, agent_name: str, blob_ref: str | None) -> SoulCacheEntry | None:
        with self._lock:
            entry = self._by_agent.get(agent_name)
            if entry is None or entry.blob_ref != blob_ref:
                return None
            return entry

    def invalidate(self, agent_name: str) -> None:
        with self._lock:
            self._by_agent.pop(agent_name, None)


_global_cache = SoulDocumentCache()


def get_soul_document_cache() -> SoulDocumentCache:
    return _global_cache