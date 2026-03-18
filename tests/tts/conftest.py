"""Shared fixtures and helpers for TTS tests."""

from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock


def write_minimal_wav(path: str, duration_ms: int = 500) -> None:
    """Write a minimal valid WAV file to *path* with given duration."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_ms / 1000)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)


def make_chunked_result(chunk_wav_paths: list[str]) -> MagicMock:
    """Return a mock voiceCLI TTSResult in chunked mode (chunk_paths set)."""
    result = MagicMock()
    result.wav_path = Path(chunk_wav_paths[0]).with_suffix(".done")  # sentinel
    result.mp3_path = None
    result.chunk_paths = [Path(p) for p in chunk_wav_paths]
    return result
