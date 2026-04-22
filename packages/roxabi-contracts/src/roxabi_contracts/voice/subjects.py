"""Voice-domain NATS subject strings and per-worker helpers.

Canonical values from ADR-044 §Subjects. Literal strings (no f-strings,
no derivation) so grep can locate every reference across the monorepo.
"""

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_worker_id

__all__ = ["SUBJECTS", "per_worker_stt", "per_worker_tts", "validate_worker_id"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of voice-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None (cf. ADR-049 §API ergonomics).

    Each field is typed as a ``Literal[...]`` — a typo in the default
    value (e.g. ``"lyra.voice.tts.reuqest"``) fails type-checking
    independently of the runtime string-equality assertions in
    ``tests/test_voice_subjects.py``.
    """

    tts_request: Literal["lyra.voice.tts.request"] = "lyra.voice.tts.request"
    tts_heartbeat: Literal["lyra.voice.tts.heartbeat"] = "lyra.voice.tts.heartbeat"
    stt_request: Literal["lyra.voice.stt.request"] = "lyra.voice.stt.request"
    stt_heartbeat: Literal["lyra.voice.stt.heartbeat"] = "lyra.voice.stt.heartbeat"
    tts_workers: Literal["tts_workers"] = "tts_workers"
    stt_workers: Literal["stt_workers"] = "stt_workers"


SUBJECTS = _Subjects()


def per_worker_tts(worker_id: str) -> str:
    """Per-worker TTS request subject: ``lyra.voice.tts.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.tts_request}.{worker_id}"


def per_worker_stt(worker_id: str) -> str:
    """Per-worker STT request subject: ``lyra.voice.stt.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.stt_request}.{worker_id}"
