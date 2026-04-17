"""Voice-domain NATS subject strings and per-worker helpers.

Canonical values from ADR-044 §Subjects. Literal strings (no f-strings,
no derivation) so grep can locate every reference across the monorepo.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of voice-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None (cf. ADR-049 §API ergonomics).
    """

    tts_request: str = "lyra.voice.tts.request"
    tts_heartbeat: str = "lyra.voice.tts.heartbeat"
    stt_request: str = "lyra.voice.stt.request"
    stt_heartbeat: str = "lyra.voice.stt.heartbeat"
    tts_workers: str = "tts_workers"
    stt_workers: str = "stt_workers"


SUBJECTS = _Subjects()


def per_worker_tts(worker_id: str) -> str:
    """Per-worker TTS request subject: ``lyra.voice.tts.request.{worker_id}``."""
    return f"{SUBJECTS.tts_request}.{worker_id}"


def per_worker_stt(worker_id: str) -> str:
    """Per-worker STT request subject: ``lyra.voice.stt.request.{worker_id}``."""
    return f"{SUBJECTS.stt_request}.{worker_id}"
