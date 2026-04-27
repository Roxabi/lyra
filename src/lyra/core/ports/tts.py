"""TtsProtocol — Domain port for text-to-speech synthesis.

Moved here from lyra.tts (V8b of hexagonal remediation, ADR-059).
lyra.tts re-exports for backward compatibility.

SynthesisResult and AgentTTSConfig are referenced as string annotations only
to keep this module free of infrastructure and adapter imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import AgentTTSConfig
    from lyra.tts import SynthesisResult


@runtime_checkable
class TtsProtocol(Protocol):
    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> "SynthesisResult": ...


__all__ = ["TtsProtocol"]
