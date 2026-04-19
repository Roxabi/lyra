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

import numpy as np
from scipy.io.wavfile import write as wav_write

from roxabi_contracts.voice import fixtures


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


def test_sample_transcript_en_is_deterministic() -> None:
    expected = "Hello, this is a roxabi-contracts test fixture."
    assert fixtures.sample_transcript_en == expected
    assert len(fixtures.sample_transcript_en) <= 120
