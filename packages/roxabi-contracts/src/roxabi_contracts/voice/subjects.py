"""Voice-domain NATS subject strings and per-worker helpers.

Canonical values from ADR-044 (absorbed into ADR-049) §Subjects.
Literal strings (no f-strings, no derivation) so grep can locate every reference
across the monorepo.
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
    value (e.g. ``"factory.voice.tts.reuqest"``) fails type-checking
    independently of the runtime string-equality assertions in
    ``tests/test_voice_subjects.py``.
    """

    tts_request: Literal["factory.voice.tts.request"] = "factory.voice.tts.request"
    tts_heartbeat: Literal["factory.voice.tts.heartbeat"] = (
        "factory.voice.tts.heartbeat"
    )
    stt_request: Literal["factory.voice.stt.request"] = "factory.voice.stt.request"
    stt_heartbeat: Literal["factory.voice.stt.heartbeat"] = (
        "factory.voice.stt.heartbeat"
    )
    tts_lifecycle_list: Literal["factory.voice.tts.lifecycle.list"] = (
        "factory.voice.tts.lifecycle.list"
    )
    tts_lifecycle_status: Literal["factory.voice.tts.lifecycle.status"] = (
        "factory.voice.tts.lifecycle.status"
    )
    stt_lifecycle_list: Literal["factory.voice.stt.lifecycle.list"] = (
        "factory.voice.stt.lifecycle.list"
    )
    stt_lifecycle_status: Literal["factory.voice.stt.lifecycle.status"] = (
        "factory.voice.stt.lifecycle.status"
    )
    tts_workers: Literal["tts_workers"] = "tts_workers"
    stt_workers: Literal["stt_workers"] = "stt_workers"


SUBJECTS = _Subjects()


def per_worker_tts(worker_id: str) -> str:
    """Per-worker TTS request subject: ``factory.voice.tts.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.tts_request}.{worker_id}"


def per_worker_stt(worker_id: str) -> str:
    """Per-worker STT request subject: ``factory.voice.stt.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.stt_request}.{worker_id}"
