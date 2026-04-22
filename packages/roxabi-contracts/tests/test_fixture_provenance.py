# pyright: reportMissingTypeStubs=false, reportMissingImports=false
"""Byte-identical provenance tests for roxabi_contracts.voice.fixtures.

ADR-049 §Fixture generation requires every binary fixture to be regenerable
from a documented generator call. This test regenerates each fixture from
its canonical recipe and asserts byte-identical equality with the value
exported by ``fixtures.py``. Any drift (a hand-edited byte, a swapped
generator, an accidental real-data seed) fails CI.
"""

from __future__ import annotations

import io
import struct

import numpy as np
from scipy.io.wavfile import write as wav_write

from roxabi_contracts.voice import fixtures

# Pin the exact wire shape so a future scipy release that changes chunk
# padding or sub-chunk ordering (making both the generator and the fixture
# drift together) fails the test rather than silently matching.
#
# Derivation: PCM WAV header is 44 bytes (RIFF 'WAVE' + fmt + data chunks),
# payload is ``16 000 samples × 2 bytes (int16)`` = 32 000 bytes.
_EXPECTED_WAV_BYTE_COUNT = 44 + 16_000 * 2
# RIFF size field at offset 4 excludes the first 8 bytes (``RIFF`` + size).
_EXPECTED_RIFF_SIZE_FIELD = _EXPECTED_WAV_BYTE_COUNT - 8


def test_silence_wav_16khz_is_byte_identical_to_generator() -> None:
    sample_rate_hz = 16_000
    duration_seconds = 1
    samples = np.zeros(sample_rate_hz * duration_seconds, dtype=np.int16)
    buf = io.BytesIO()
    wav_write(buf, sample_rate_hz, samples)
    expected = buf.getvalue()

    assert fixtures.silence_wav_16khz == expected


def test_silence_wav_16khz_has_riff_header() -> None:
    assert fixtures.silence_wav_16khz.startswith(b"RIFF")


def test_silence_wav_16khz_byte_count_is_pinned() -> None:
    assert len(fixtures.silence_wav_16khz) == _EXPECTED_WAV_BYTE_COUNT


def test_silence_wav_16khz_riff_size_field_is_pinned() -> None:
    (riff_size,) = struct.unpack_from("<I", fixtures.silence_wav_16khz, 4)
    assert riff_size == _EXPECTED_RIFF_SIZE_FIELD


def test_sample_transcript_en_is_deterministic() -> None:
    expected = "Hello, this is a roxabi-contracts test fixture."
    assert fixtures.sample_transcript_en == expected
    assert len(fixtures.sample_transcript_en) <= 120
