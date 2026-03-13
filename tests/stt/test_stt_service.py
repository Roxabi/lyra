"""Tests for STTService and STTConfig (voiceCLI backend)."""

from __future__ import annotations

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
# STTConfig defaults
# ---------------------------------------------------------------------------


def test_config_default_model():
    cfg = STTConfig()
    assert cfg.model_size == "large-v3-turbo"


# ---------------------------------------------------------------------------
# load_stt_config() env vars
# ---------------------------------------------------------------------------


def test_load_stt_config_defaults(monkeypatch):
    monkeypatch.delenv("STT_MODEL_SIZE", raising=False)
    cfg = load_stt_config()
    assert cfg.model_size == "large-v3-turbo"


def test_load_stt_config_from_env(monkeypatch):
    monkeypatch.setenv("STT_MODEL_SIZE", "medium")
    cfg = load_stt_config()
    assert cfg.model_size == "medium"


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
# STTService init
# ---------------------------------------------------------------------------


def test_stt_service_stores_model():
    svc = STTService(STTConfig(model_size="medium"))
    assert svc._model == "medium"


# ---------------------------------------------------------------------------
# STTService.transcribe() — delegates to voiceCLI
# ---------------------------------------------------------------------------


def _make_vc_result(text="Hello world", language: str | None = "en", segments=None):
    result = MagicMock()
    result.text = text
    result.language = language
    result.segments = (
        [{"start": 0.0, "end": 2.1, "text": text}] if segments is None else segments
    )
    return result


@pytest.mark.asyncio
async def test_transcribe_returns_transcription_result():
    svc = STTService(STTConfig())

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=None),
        patch(
            "voicecli.transcribe.transcribe", return_value=_make_vc_result()
        ) as mock_t,
    ):
        result = await svc.transcribe("/tmp/fake.ogg")

    assert isinstance(result, TranscriptionResult)
    assert result.text == "Hello world"
    assert result.language == "en"
    assert result.duration_seconds == pytest.approx(2.1)
    mock_t.assert_called_once()


@pytest.mark.asyncio
async def test_transcribe_passes_vocab_as_prompt():
    svc = STTService(STTConfig())

    with (
        patch("voicecli.config.load_vocab", return_value=["Lyra", "Roxabi"]),
        patch("voicecli.config.vocab_to_prompt", return_value="Lyra, Roxabi."),
        patch(
            "voicecli.transcribe.transcribe", return_value=_make_vc_result()
        ) as mock_t,
    ):
        await svc.transcribe("/tmp/fake.ogg")

    _, kwargs = mock_t.call_args
    assert kwargs.get("initial_prompt") == "Lyra, Roxabi."


@pytest.mark.asyncio
async def test_transcribe_none_language_becomes_unknown():
    svc = STTService(STTConfig())

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=None),
        patch(
            "voicecli.transcribe.transcribe",
            return_value=_make_vc_result(language=None),
        ),
    ):
        result = await svc.transcribe("/tmp/fake.ogg")

    assert result.language == "unknown"


@pytest.mark.asyncio
async def test_transcribe_empty_segments_duration_zero():
    svc = STTService(STTConfig())

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=None),
        patch(
            "voicecli.transcribe.transcribe",
            return_value=_make_vc_result(segments=[]),
        ),
    ):
        result = await svc.transcribe("/tmp/fake.ogg")

    assert result.duration_seconds == 0.0


@pytest.mark.asyncio
async def test_transcribe_propagates_error():
    svc = STTService(STTConfig())

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=None),
        patch(
            "voicecli.transcribe.transcribe",
            side_effect=RuntimeError("model load failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="model load failed"):
            await svc.transcribe("/tmp/fake.ogg")
