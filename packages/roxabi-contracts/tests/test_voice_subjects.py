"""Tests for roxabi_contracts.voice subjects namespace.

ADR-044 defines the canonical subject strings used by lyra's NatsTtsClient /
NatsSttClient and voiceCLI's TtsNatsAdapter / SttNatsAdapter. This file
locks those strings so a typo or accidental rename fails here, not silently
in production.
"""

import pytest

from roxabi_contracts.voice import SUBJECTS
from roxabi_contracts.voice.subjects import per_worker_stt, per_worker_tts


def test_tts_request_subject() -> None:
    assert SUBJECTS.tts_request == "lyra.voice.tts.request"


def test_tts_heartbeat_subject() -> None:
    assert SUBJECTS.tts_heartbeat == "lyra.voice.tts.heartbeat"


def test_stt_request_subject() -> None:
    assert SUBJECTS.stt_request == "lyra.voice.stt.request"


def test_stt_heartbeat_subject() -> None:
    assert SUBJECTS.stt_heartbeat == "lyra.voice.stt.heartbeat"


def test_queue_group_constants() -> None:
    assert SUBJECTS.tts_workers == "tts_workers"
    assert SUBJECTS.stt_workers == "stt_workers"


def test_per_worker_helpers() -> None:
    assert per_worker_tts("w1") == "lyra.voice.tts.request.w1"
    assert per_worker_stt("w2") == "lyra.voice.stt.request.w2"


_UNSAFE_WORKER_IDS = [
    ("*", "wildcard-star"),
    (">", "wildcard-subtree"),
    ("a.b", "single-dot"),
    ("worker.nested", "double-token"),
    ("", "empty"),
    ("with space", "space"),
    ("w/slash", "slash"),
    ("w\x00null", "null-byte"),
]


@pytest.mark.parametrize(
    "bad_id",
    [bad for bad, _ in _UNSAFE_WORKER_IDS],
    ids=[label for _, label in _UNSAFE_WORKER_IDS],
)
def test_per_worker_tts_rejects_unsafe_worker_id(bad_id: str) -> None:
    """NATS subject injection guard — see subjects.py::_validate_worker_id."""
    with pytest.raises(ValueError, match="[A-Za-z0-9_-]"):
        per_worker_tts(bad_id)


@pytest.mark.parametrize(
    "bad_id",
    ["*", ">", "a.b", "", "with space"],
    ids=["wildcard-star", "wildcard-subtree", "dotted", "empty", "space"],
)
def test_per_worker_stt_rejects_unsafe_worker_id(bad_id: str) -> None:
    """NATS subject injection guard — see subjects.py::_validate_worker_id."""
    with pytest.raises(ValueError, match="[A-Za-z0-9_-]"):
        per_worker_stt(bad_id)


def test_voice_init_does_not_pull_nats_transports() -> None:
    """Spec AC — Package surface: voice/__init__.py imports no transport code.

    Fresh subprocess import of ``roxabi_contracts.voice`` must not load any
    ``nats.*`` or ``roxabi_nats.*`` module. Guards the pure-Pydantic
    invariant from a silent regression where a stray transport import
    sneaks into models.py or subjects.py.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.voice, sys; "
                "bad = [m for m in sys.modules if m.startswith('nats') or "
                "m.startswith('roxabi_nats')]; "
                "assert not bad, f'transport modules leaked: {bad!r}'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_voice_init_does_not_expose_fixtures() -> None:
    """Fixtures are test-only — must NOT be in voice package surface.

    Checked two ways: (1) ``fixtures`` is not advertised in ``__all__``
    (what ``from roxabi_contracts.voice import *`` would see), and (2) a
    subprocess with a fresh interpreter that imports
    ``roxabi_contracts.voice`` does NOT pull ``.fixtures`` into
    ``sys.modules``. The subprocess step guards against pytest's
    session-level import cache masking a regression where another test's
    ``from roxabi_contracts.voice.fixtures import ...`` has already
    populated ``vars(voice_mod)`` via Python's submodule-attr behavior.
    """
    import subprocess
    import sys

    import roxabi_contracts.voice as voice_mod

    assert "fixtures" not in voice_mod.__all__

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.voice, sys; "
                "assert 'roxabi_contracts.voice.fixtures' not in sys.modules, "
                "'voice/__init__.py must not import fixtures'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
