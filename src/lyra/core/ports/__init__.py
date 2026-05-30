"""Driven ports — Protocols the domain uses to consume external capabilities.

Taxonomy
--------
``core/ports/`` houses **driven (secondary) ports** in the canonical Cockburn
sense: the domain *drives* these to talk to the outside world (LLM inference,
TTS, STT, audit sink, …). Every port here is a pure Protocol — no
infrastructure import (TYPE_CHECKING-only is permitted for types).

Protocols that describe an **internal collaboration** — e.g. ``ChannelAdapter``
in ``core/hub/``, ``PipelineMiddleware`` in ``core/hub/middleware/``,
``PoolContext`` in ``core/pool/`` — are **role interfaces** (Fowler) co-located
with their sub-domain. They are *not* driven ports and must NOT migrate here.

See ``src/lyra/core/CLAUDE.md`` for the full glossary and the future
"orthodoxie pure" note (split ``ChannelAdapter`` into a driver inbound port
and a driven outbound port).

Constraints
-----------
- Pure Protocol definitions only — no concrete classes, no infrastructure imports.
- New driven ports belong here; new role interfaces belong alongside the
  sub-domain they serve.
"""

from lyra.core.ports.audit_sink import AuditSink
from lyra.core.ports.blobstore import BlobStorePort
from lyra.core.ports.llm import LlmProvider, LlmResult
from lyra.core.ports.resume_publisher import ResumePublisherPort
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol

__all__ = [
    "AuditSink",
    "BlobStorePort",
    "LlmProvider",
    "LlmResult",
    "ResumePublisherPort",
    "STTProtocol",
    "TtsProtocol",
]
