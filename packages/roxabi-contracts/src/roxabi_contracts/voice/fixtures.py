# pyright: reportMissingTypeStubs=false
"""Test fixtures for roxabi_contracts.voice.

Pure synthesized data. NO real user audio, NO binary blobs committed,
NO NATS imports. scipy is declared in [project.optional-dependencies].testing
— production installs that do not request the testing extra will NOT pull
this module's imports (fixtures.py is only loaded when tests import it).

The pyright directive above is scoped to this file only. scipy/numpy ship
stubs via PyPI packages that strict-mode pyright does not always pick up
in a workspace lockfile; the suppression is scoped rather than global so
production code elsewhere retains full strict typing.
"""

from __future__ import annotations

import io

try:
    import numpy as np
    from scipy.io.wavfile import write as _wav_write
except ImportError as exc:  # pragma: no cover — exercised only without [testing]
    raise ImportError(
        "roxabi_contracts.voice.fixtures requires the `[testing]` extra "
        "(numpy + scipy). Install with: `uv pip install roxabi-contracts[testing]` "
        "or `uv sync --all-extras` inside the workspace."
    ) from exc

_SAMPLE_RATE_HZ: int = 16_000
_DURATION_SECONDS: int = 1


def _build_silence_wav_16khz() -> bytes:
    """Synthesize 1 s of 16 kHz mono int16 silence as a WAV bytes buffer."""
    samples = np.zeros(_SAMPLE_RATE_HZ * _DURATION_SECONDS, dtype=np.int16)
    buf = io.BytesIO()
    _wav_write(buf, _SAMPLE_RATE_HZ, samples)
    return buf.getvalue()


silence_wav_16khz: bytes = _build_silence_wav_16khz()
"""1 s of 16 kHz mono int16 silence, WAV-encoded. Header starts with ``b'RIFF'``."""

sample_transcript_en: str = "Hello, this is a roxabi-contracts test fixture."
"""Deterministic English transcript for STT fixtures. No PII, ≤120 chars."""
