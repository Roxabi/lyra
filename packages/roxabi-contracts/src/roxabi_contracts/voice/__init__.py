"""Voice-domain NATS contract surface.

Public API: SUBJECTS + four envelope models. The `fixtures` submodule is
test-only and DELIBERATELY not re-exported here — it must be imported
explicitly as ``from roxabi_contracts.voice.fixtures import ...``.
"""

from roxabi_contracts.voice.models import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.subjects import SUBJECTS

__all__ = [
    "SUBJECTS",
    "SttRequest",
    "SttResponse",
    "TtsRequest",
    "TtsResponse",
]
