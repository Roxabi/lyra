"""AgentRefiner: LLM-guided interactive profile refinement for Lyra agents."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lyra.core.agent_models import AgentRow
    from lyra.core.stores.agent_store import AgentStore

__all__ = [
    "AgentRefiner",
    "LlmProvider",
    "REFINABLE_FIELDS",
    "RefinementCancelled",
    "RefinementContext",
    "RefinementPatch",
    "SdkLlmProvider",
    "TerminalIO",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RefinementCancelled(Exception):
    """Raised when the operator aborts an interactive refinement session."""


# ---------------------------------------------------------------------------
# Allow-list of patchable AgentRow fields
# ---------------------------------------------------------------------------

REFINABLE_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "persona",
        "persona_json",
        "tts_json",
        "stt_json",
        "patterns_json",
        "passthroughs_json",
        "plugins_json",
        "fallback_language",
        "tools_json",
        "max_turns",
        "streaming",
        "i18n_language",
        "memory_namespace",
        "workspaces_json",
        "commands_json",
    }
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefinementContext:
    """Snapshot of an agent's refinable profile fields."""

    agent_name: str
    persona: str | None  # AgentRow.persona (name string)
    persona_json: str | None  # AgentRow.persona_json (rich JSON string)
    tts_json: str | None  # AgentRow.tts_json
    stt_json: str | None  # AgentRow.stt_json
    model: str
    passthroughs: list[str]  # parsed from AgentRow.passthroughs_json
    patterns: dict  # parsed from AgentRow.patterns_json
    plugins: list[str]  # parsed from AgentRow.plugins_json


@dataclass
class RefinementPatch:
    """Proposed field updates to apply to an AgentRow."""

    fields: dict[str, Any]  # maps AgentRow field names → new values

    def __post_init__(self) -> None:
        unknown = set(self.fields) - REFINABLE_FIELDS
        if unknown:
            raise ValueError(
                f"Unknown or disallowed AgentRow field(s): {sorted(unknown)}. "
                f"Patchable fields: {sorted(REFINABLE_FIELDS)}"
            )

    def as_json(self) -> str:
        """Return JSON string of fields dict."""
        return json.dumps(self.fields)

    def to_agent_row(self, existing: "AgentRow") -> "AgentRow":
        """Apply patch to existing row using dataclasses.replace()."""
        return dataclasses.replace(existing, **self.fields)


# ---------------------------------------------------------------------------
# I/O and LLM provider abstractions
# ---------------------------------------------------------------------------


class TerminalIO:
    """Simple terminal I/O wrapper (injectable for tests)."""

    def prompt(self, text: str) -> str:
        return input(text)

    def print(self, text: str) -> None:
        print(text)


class LlmProvider(Protocol):
    """Protocol for any LLM backend used by AgentRefiner."""

    def chat(self, system: str, messages: list[dict[str, Any]]) -> str:
        """Single LLM call returning assistant response text."""
        ...


class SdkLlmProvider:
    """Anthropic SDK-based LLM provider (sync)."""

    def __init__(
        self,
        api_key: str,
        # Haiku: lowest latency/cost for interactive CLI session
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def chat(self, system: str, messages: list[dict[str, Any]]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=messages,  # type: ignore[arg-type]
        )
        for block in response.content:
            if hasattr(block, "text"):
                return block.text  # type: ignore[union-attr]
        return ""


# ---------------------------------------------------------------------------
# AgentRefiner
# ---------------------------------------------------------------------------

_CONFIRM_WORDS: frozenset[str] = frozenset(
    {"confirm", "yes", "done", "apply", "ok", "y"}
)


class AgentRefiner:
    """LLM-guided interactive profile refinement session for a named agent."""

    def __init__(
        self,
        name: str,
        store: "AgentStore",
        driver: LlmProvider | None = None,
    ) -> None:
        self._name = name
        self._store = store
        self._driver = driver  # injected or auto-detected at session time

    # ------------------------------------------------------------------
    # Profile read
    # ------------------------------------------------------------------

    def read_profile(self) -> RefinementContext:
        """Read agent from store cache → RefinementContext.

        Raises ValueError if agent not found.
        """
        row = self._store.get(self._name)
        if row is None:
            raise ValueError(f"Agent {self._name!r} not found in DB")
        return RefinementContext(
            agent_name=row.name,
            persona=row.persona,
            persona_json=row.persona_json,
            tts_json=row.tts_json,
            stt_json=row.stt_json,
            model=row.model,
            passthroughs=json.loads(row.passthroughs_json)  # type: ignore[attr-defined]
            if getattr(row, "passthroughs_json", None)
            else [],
            patterns=json.loads(row.patterns_json) if row.patterns_json else {},
            plugins=json.loads(row.plugins_json) if row.plugins_json else [],
        )

    # ------------------------------------------------------------------
    # Interactive session
    # ------------------------------------------------------------------

    def run_session(self, io: TerminalIO, *, max_turns: int = 20) -> RefinementPatch:
        """LLM-driven Q&A loop. Returns patch on user confirmation.

        Flow:
        1. Read profile.
        2. Build system prompt with current profile.
        3. LLM greets operator with plain-language summary + asks what to change.
        4. Operator responds, LLM proposes changes.
        5. Operator confirms → LLM outputs <<PATCH>>...<<END_PATCH>> JSON block.
        6. Parse and return RefinementPatch.

        The operator can type "quit", "exit", or "abort" to cancel (raises
        KeyboardInterrupt).  When the operator types "confirm", "yes", or "done",
        the LLM should output changes summary followed by a patch block.
        """
        driver = self._driver or self._resolve_driver()
        ctx = self.read_profile()

        system = self._build_system_prompt(ctx)
        messages: list[dict[str, Any]] = []

        # Initial greeting
        initial_msg = "Hello, I'd like to refine this agent's profile."
        initial_response = driver.chat(
            system, [{"role": "user", "content": initial_msg}]
        )
        io.print(initial_response)
        messages.append({"role": "user", "content": initial_msg})
        messages.append({"role": "assistant", "content": initial_response})

        turn = 0
        while turn < max_turns:
            turn += 1
            user_input = io.prompt("\nYou: ").strip()
            if not user_input:
                turn -= 1  # don't count empty prompts against the limit
                continue
            if user_input.lower() in ("quit", "exit", "abort"):
                raise RefinementCancelled("Session aborted by user.")

            waiting_for_confirmation = user_input.lower().strip(".!") in _CONFIRM_WORDS

            messages.append({"role": "user", "content": user_input})
            response = driver.chat(system, messages)
            io.print(f"\nAssistant: {response}")
            messages.append({"role": "assistant", "content": response})

            # Only extract patch when operator explicitly confirmed
            if waiting_for_confirmation:
                patch = self._extract_patch(response)
                if patch is not None:
                    return patch

        raise RuntimeError(
            f"Max turns ({max_turns}) reached without a confirmed patch. "
            "Try again or use 'lyra agent patch' directly."
        )

    # ------------------------------------------------------------------
    # Patch apply
    # ------------------------------------------------------------------

    def apply_patch(self, patch: RefinementPatch) -> "AgentRow":
        """Apply patch to agent row and upsert to DB. Returns updated AgentRow.

        Internally calls asyncio.run() — consistent with the sync CLI pattern.
        """
        import asyncio

        async def _apply() -> "AgentRow":
            row = self._store.get(self._name)
            if row is None:
                raise ValueError(f"Agent {self._name!r} not found in DB")
            updated = patch.to_agent_row(row)
            await self._store.upsert(updated)
            return updated

        return asyncio.run(_apply())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(ctx: RefinementContext) -> str:
        lines = [
            "You are an AI assistant helping an operator refine an AI agent profile.",
            "",
            f"Current profile for agent '{ctx.agent_name}':",
            f"  model: {ctx.model}",
            f"  persona: {ctx.persona or '(none)'}",
        ]
        if ctx.persona_json:
            try:
                p = json.loads(ctx.persona_json)
                identity = p.get("identity", {})
                if identity.get("display_name"):
                    lines.append(f"  display_name: {identity['display_name']}")
                if identity.get("role"):
                    lines.append(f"  role: {identity['role']}")
            except Exception:  # noqa: BLE001
                pass
        if ctx.tts_json:
            try:
                lines.append(f"  tts: {json.loads(ctx.tts_json)}")
            except Exception:  # noqa: BLE001
                pass
        if ctx.stt_json:
            try:
                lines.append(f"  stt: {json.loads(ctx.stt_json)}")
            except Exception:  # noqa: BLE001
                pass
        lines.extend(
            [
                f"  plugins: {ctx.plugins}",
                f"  passthroughs: {ctx.passthroughs}",
                "",
                "Your job:",
                "1. Present the current profile in plain language.",
                "2. Ask what the operator would like to change.",
                "3. Propose specific, concrete field updates.",
                "4. When the operator confirms changes (says 'yes', 'confirm',"
                " or 'done'),",
                "   output a summary followed by a JSON patch block:",
                "   <<PATCH>>",
                '   {"field_name": "new_value"}',
                "   <<END_PATCH>>",
                "",
                "Valid patchable AgentRow fields: model, persona, persona_json,",
                "tts_json, stt_json, patterns_json, passthroughs_json,",
                "plugins_json, fallback_language, tools_json, max_turns, streaming,",
                "i18n_language, memory_namespace, workspaces_json, commands_json.",
                "",
                "JSON field values must be valid strings"
                " (JSON arrays/objects as JSON strings).",
                "Be specific, concise, and helpful.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_patch(text: str) -> RefinementPatch | None:
        """Extract <<PATCH>>...<<END_PATCH>> block from LLM response."""
        if "<<PATCH>>" not in text or "<<END_PATCH>>" not in text:
            return None
        try:
            raw = text.split("<<PATCH>>", 1)[1].split("<<END_PATCH>>", 1)[0].strip()
            fields = json.loads(raw)
            if not isinstance(fields, dict):
                return None
            return RefinementPatch(fields=fields)
        except (json.JSONDecodeError, IndexError, ValueError):
            return None

    def _resolve_driver(self) -> LlmProvider:
        """Auto-detect LLM provider from environment."""
        import os

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            return SdkLlmProvider(api_key=api_key)
        raise RuntimeError(
            "No LLM provider configured for agent refiner.\n"
            "Set ANTHROPIC_API_KEY or pass a driver= to AgentRefiner()."
        )
