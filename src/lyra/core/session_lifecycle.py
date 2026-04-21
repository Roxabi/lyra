from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent.agent_config import Agent
    from .memory import MemoryManager, SessionSnapshot
    from .pool import Pool

log = logging.getLogger(__name__)

MODEL_CONTEXT_TOKENS = 200_000
COMPACT_THRESHOLD = int(0.8 * MODEL_CONTEXT_TOKENS)
COMPACT_TAIL = 10


class SessionManager:
    """Mixin owning session flush, compaction, and extraction.

    Designed for use as a mixin with ``AgentBase`` only.
    Attribute stubs below are satisfied by ``AgentBase.__init__``.
    """

    config: Agent
    _memory: MemoryManager | None = None
    _task_registry: set | None = None
    _compact_context_tokens: int = MODEL_CONTEXT_TOKENS

    # S4 — session flush (issue #83)

    async def flush_session(self, pool: "Pool", reason: str = "end") -> None:
        """Write final session summary to L3. No-op if no memory or no user."""
        if self._memory is None or pool.user_id == "":
            return
        snap = pool.snapshot(self.config.memory_namespace)
        summary = await self._summarize_session(pool)
        await self._memory.upsert_session(snap, summary, status="final")
        await self._memory.upsert_contact(
            snap.user_id, snap.medium, snap.agent_namespace
        )
        if snap.source_turns >= 3:
            self._schedule_extraction(snap, summary, self._task_registry)

    async def _summarize_session(self, pool: "Pool") -> str:
        """Generate session summary. Base: simple truncation. Override for LLM."""
        turns = list(pool.sdk_history)[-20:]
        text = "\n".join(str(t.get("content", "")) for t in turns)
        log.warning(
            "_summarize_session not overridden — storing truncated transcript"
            " for pool %s; override with an LLM call for production memory quality",
            pool.pool_id,
        )
        return f"Session summary ({pool.message_count} messages): {text[:500]}"

    def _schedule_extraction(
        self,
        snap: "SessionSnapshot",
        summary: str,
        task_registry: set | None = None,
    ) -> None:
        """Schedule background concept + preference extraction tasks."""
        for coro in [
            self._run_concept_extraction(snap, summary),
            self._run_preference_extraction(snap, summary),
        ]:
            task = asyncio.create_task(coro)
            if task_registry is not None:
                task_registry.add(task)
                task.add_done_callback(task_registry.discard)

    # S5 — compaction (issue #83)

    async def compact(self, pool: "Pool") -> None:
        """Summarize and truncate pool history when approaching context limit."""
        if self._memory is None:
            return
        token_est = sum(len(str(t.get("content", ""))) // 4 for t in pool.sdk_history)
        # Also trigger compaction when sdk_history has many entries (each entry
        # carries metadata overhead that adds up regardless of content size).
        if token_est <= int(0.8 * self._compact_context_tokens):
            return
        summary = await self._summarize_session(pool)
        snap = pool.snapshot(self.config.memory_namespace)
        await self._memory.upsert_session(snap, summary, status="partial")
        tail = list(pool.sdk_history)[-COMPACT_TAIL:]
        pool.sdk_history.clear()
        pool.sdk_history.extend([{"role": "system", "content": summary}] + tail)

    # S7 — concept + preference extraction (issue #83)

    async def _extraction_llm_call(self, prompt: str) -> str:
        """LLM call for extraction. Base: no-op. Overridden by concrete agents."""
        log.debug(
            "_extraction_llm_call not overridden — extraction skipped;"
            " override in concrete agent"
        )
        return "[]"

    async def _run_concept_extraction(
        self, snap: "SessionSnapshot", summary: str
    ) -> None:
        try:
            prompt = (
                f"Extract concepts from this session summary as JSON array.\n"
                f'Each item: {{"name": str, "category": str, "content": str, '
                f'"relations": [], "confidence": float}}\n'
                f"Categories: technology|project|decision|fact|entity\n"
                f"Min confidence: 0.7. Return [] if nothing worth extracting.\n\n"
                f"{summary}"
            )
            raw = await self._extraction_llm_call(prompt)
            concepts = json.loads(raw)
            if self._memory is not None:
                for concept in concepts:
                    if not isinstance(concept, dict):
                        log.warning(
                            "concept extraction: skipping non-dict item: %r",
                            type(concept),
                        )
                        continue
                    if not concept.get("name") or not concept.get("content"):
                        log.warning(
                            "concept extraction: skipping item missing required"
                            " fields: %r",
                            list(concept.keys()),
                        )
                        continue
                    if concept.get("confidence", 0) >= 0.7:
                        await self._memory.upsert_concept(snap, concept)
        except Exception:
            log.warning(
                "concept extraction failed for session %s",
                snap.session_id,
                exc_info=True,
            )

    async def _run_preference_extraction(
        self, snap: "SessionSnapshot", summary: str
    ) -> None:
        try:
            prompt = (
                "Extract explicit user preferences from this session summary"
                " as JSON array.\n"
                f'Each item: {{"name": str, "domain": str, "strength": float, '
                f'"source": str, "content": str}}\n'
                f"Domains: communication|technical|workflow\n"
                f"Only explicit stated preferences. Return [] if none.\n\n{summary}"
            )
            raw = await self._extraction_llm_call(prompt)
            prefs = json.loads(raw)
            if self._memory is not None:
                for pref in prefs:
                    if not isinstance(pref, dict):
                        log.warning(
                            "preference extraction: skipping non-dict item: %r",
                            type(pref),
                        )
                        continue
                    if not pref.get("name"):
                        log.warning(
                            "preference extraction: skipping item missing 'name': %r",
                            list(pref.keys()),
                        )
                        continue
                    await self._memory.upsert_preference(snap, pref)
        except Exception:
            log.warning(
                "preference extraction failed for session %s",
                snap.session_id,
                exc_info=True,
            )
