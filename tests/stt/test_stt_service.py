"""Tests for STTService and STTConfig (V2 — issue #148)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lyra.stt import (
    STTConfig,
    STTService,
    TranscriptionResult,
    is_whisper_noise,
    load_stt_config,
)

# ---------------------------------------------------------------------------
# STTConfig.validate()
# ---------------------------------------------------------------------------


def test_config_validate_float16_cpu_raises():
    with pytest.raises(ValueError, match="float16"):
        STTConfig(device="cpu", compute_type="float16").validate()


def test_config_validate_int8_cuda_raises():
    with pytest.raises(ValueError, match="int8"):
        STTConfig(device="cuda", compute_type="int8").validate()


def test_config_validate_cpu_int8_is_valid():
    STTConfig(device="cpu", compute_type="int8").validate()


def test_config_validate_cuda_float16_is_valid():
    STTConfig(device="cuda", compute_type="float16").validate()


def test_config_validate_auto_auto_is_valid():
    STTConfig(device="auto", compute_type="auto").validate()


def test_config_validate_invalid_device_raises():
    with pytest.raises(ValueError, match="device="):
        STTConfig(device="cude", compute_type="auto").validate()


# ---------------------------------------------------------------------------
# load_stt_config() env vars
# ---------------------------------------------------------------------------


def test_load_stt_config_defaults(monkeypatch):
    monkeypatch.delenv("STT_MODEL_SIZE", raising=False)
    monkeypatch.delenv("STT_DEVICE", raising=False)
    monkeypatch.delenv("STT_COMPUTE_TYPE", raising=False)
    cfg = load_stt_config()
    assert cfg.model_size == "small"
    assert cfg.device == "auto"
    assert cfg.compute_type == "auto"


def test_load_stt_config_from_env(monkeypatch):
    monkeypatch.setenv("STT_MODEL_SIZE", "large-v3")
    monkeypatch.setenv("STT_DEVICE", "cuda")
    monkeypatch.setenv("STT_COMPUTE_TYPE", "float16")
    cfg = load_stt_config()
    assert cfg.model_size == "large-v3"
    assert cfg.device == "cuda"
    assert cfg.compute_type == "float16"


# ---------------------------------------------------------------------------
# is_whisper_noise()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", True),
        ("   ", True),
        ("[music]", True),
        ("[MUSIC]", True),
        ("[silence]", True),
        ("[noise]", True),
        ("[applause]", True),
        ("[laughter]", True),
        ("Hello world", False),
        (" some text ", False),
    ],
)
def test_is_whisper_noise(text, expected):
    assert is_whisper_noise(text) is expected


# ---------------------------------------------------------------------------
# STTService — device resolution
# ---------------------------------------------------------------------------


def test_stt_service_gpu_fallback_to_cpu_when_torch_unavailable():
    """When torch is not importable, device resolves to cpu."""
    with patch.dict("sys.modules", {"torch": None}):
        svc = STTService(STTConfig(device="auto"))
    assert svc._device == "cpu"
    assert svc._compute_type == "int8"


def test_stt_service_cuda_when_torch_available():
    """When torch.cuda.is_available() returns True, device resolves to cuda."""
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    with patch.dict("sys.modules", {"torch": mock_torch}):
        svc = STTService(STTConfig(device="auto"))
    assert svc._device == "cuda"
    assert svc._compute_type == "float16"


def test_stt_service_explicit_device_not_overridden():
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))
    assert svc._device == "cpu"
    assert svc._compute_type == "int8"


def test_stt_service_auto_cpu_when_cuda_unavailable():
    """When torch present but cuda.is_available() is False, device resolves to cpu."""
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False
    with patch.dict("sys.modules", {"torch": mock_torch}):
        svc = STTService(STTConfig(device="auto"))
    assert svc._device == "cpu"
    assert svc._compute_type == "int8"


# ---------------------------------------------------------------------------
# STTService.transcribe() — joins segments, returns TranscriptionResult
# ---------------------------------------------------------------------------


def _make_segment(text: str):
    seg = SimpleNamespace(text=text)
    return seg


def _make_info(language="en", duration=3.5):
    return SimpleNamespace(language=language, duration=duration)


@pytest.mark.asyncio
async def test_transcribe_joins_segments():
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))

    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [_make_segment("Hello"), _make_segment(" world")],
        _make_info(language="en", duration=2.1),
    )
    svc._model = mock_model

    result = await svc.transcribe("/tmp/fake.ogg")

    assert isinstance(result, TranscriptionResult)
    assert result.text == "Hello world"
    assert result.language == "en"
    assert result.duration_seconds == pytest.approx(2.1)


@pytest.mark.asyncio
async def test_transcribe_strips_whitespace():
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))

    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [_make_segment("  trimmed  ")],
        _make_info(),
    )
    svc._model = mock_model

    result = await svc.transcribe("/tmp/x.ogg")
    assert result.text == "trimmed"


@pytest.mark.asyncio
async def test_transcribe_empty_segments():
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], _make_info())
    svc._model = mock_model

    result = await svc.transcribe("/tmp/x.ogg")
    assert result.text == ""
    assert is_whisper_noise(result.text)


# ---------------------------------------------------------------------------
# STTService — lazy model loading
# ---------------------------------------------------------------------------


def test_model_not_loaded_at_init():
    """Model must NOT be loaded during __init__ (lazy)."""
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))
    assert svc._model is None


# ---------------------------------------------------------------------------
# STTService.transcribe() — error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_propagates_model_error():
    """Errors from WhisperModel.transcribe() propagate to the caller."""
    svc = STTService(STTConfig(device="cpu", compute_type="int8"))
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("CUDA OOM")
    svc._model = mock_model

    with pytest.raises(RuntimeError, match="CUDA OOM"):
        await svc.transcribe("/tmp/fake.ogg")
