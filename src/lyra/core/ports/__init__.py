"""Capability ports — Protocols the domain uses to consume an external capability.

Taxonomy rule
-------------
core/ports/ = **capability ports**: interfaces for external capabilities that the
domain consumes (LLM inference, TTS, STT, audit, …). Every port here is pure
Protocol with no infrastructure import (TYPE_CHECKING-only is permitted for types).

**Orchestration ports** — Protocols that define a role local to a sub-domain
(e.g. ChannelAdapter in core/hub/, PipelineMiddleware in core/hub/middleware/) —
are intentionally co-located with their sub-domain and must NOT migrate here.

Constraints
-----------
- Pure Protocol definitions only — no concrete classes, no infrastructure imports.
- New capability interfaces belong here; new orchestration interfaces belong
  alongside the sub-domain they serve.
"""

from lyra.core.ports.llm import LlmProvider, LlmResult
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol

__all__ = [
    "LlmProvider",
    "LlmResult",
    "STTProtocol",
    "TtsProtocol",
]
