"""Tests for _wav_duration_ms() utility."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from lyra.tts import _wav_duration_ms

from .conftest import write_minimal_wav

# ---------------------------------------------------------------------------
# _wav_duration_ms() — utility
# ---------------------------------------------------------------------------


def test_wav_duration_ms_none_on_bad_file():
    """_wav_duration_ms returns None for a nonexistent path."""
    assert _wav_duration_ms(Path("/tmp/__nonexistent_lyra_tts_test__.wav")) is None


def test_wav_duration_ms_none_on_corrupt_file():
    """_wav_duration_ms returns None when the file is not a valid WAV."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(b"not a wav file at all \x00\x01\x02")
        tmp_path = tmp.name

    try:
        assert _wav_duration_ms(Path(tmp_path)) is None
    finally:
        os.unlink(tmp_path)


def test_wav_duration_ms_valid_file():
    """_wav_duration_ms returns a positive integer for a valid WAV file."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_minimal_wav(tmp_path, duration_ms=750)
        result = _wav_duration_ms(Path(tmp_path))
        assert result is not None
        assert isinstance(result, int)
        assert result > 0
    finally:
        os.unlink(tmp_path)
