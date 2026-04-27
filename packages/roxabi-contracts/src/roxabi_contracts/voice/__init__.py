"""Voice-domain NATS contract surface.

Public API: SUBJECTS namespace + per_worker_* helpers + four envelope
models. The `fixtures` submodule is test-only and DELIBERATELY not
re-exported here — it must be imported explicitly as
``from roxabi_contracts.voice.fixtures import ...``.
"""

from roxabi_contracts.voice.builders import (
    build_stt_response,
    build_tts_response,
)
from roxabi_contracts.voice.constants import (
    AGENT_TTS_FIELDS,
    TTS_CONFIG_FIELDS,
)
from roxabi_contracts.voice.models import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.subjects import (
    SUBJECTS,
    per_worker_stt,
    per_worker_tts,
    validate_worker_id,
)

__all__ = [
    "AGENT_TTS_FIELDS",
    "SUBJECTS",
    "TTS_CONFIG_FIELDS",
    "SttRequest",
    "SttResponse",
    "TtsRequest",
    "TtsResponse",
    "build_stt_response",
    "build_tts_response",
    "per_worker_stt",
    "per_worker_tts",
    "validate_worker_id",
]
