"""Tests for roxabi_contracts.voice subjects namespace.

ADR-044 defines the canonical subject strings used by lyra's NatsTtsClient /
NatsSttClient and voiceCLI's TtsNatsAdapter / SttNatsAdapter. This file
locks those strings so a typo or accidental rename fails here, not silently
in production.
"""

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
