"""Tests for STTService and STTConfig (voiceCLI backend)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyra.stt import (
    STTConfig,
    STTService,
    TranscriptionResult,
    is_whisper_noise,
    load_stt_config,
)

_has_voicecli = importlib.util.find_spec("voicecli") is not None
requires_voicecli = pytest.mark.skipif(
    not _has_voicecli, reason="voicecli not installed (optional voice extra)"
)

# ---------------------------------------------------------------------------
# STTConfig defaults
# ---------------------------------------------------------------------------


def test_config_default_model():
    cfg = STTConfig(model_size="large-v3-turbo")
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


@requires_voicecli
@pytest.mark.asyncio
async def test_transcribe_returns_transcription_result():
    svc = STTService(STTConfig(model_size="large-v3-turbo"))

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


@requires_voicecli
@pytest.mark.asyncio
async def test_transcribe_passes_vocab_as_prompt():
    svc = STTService(STTConfig(model_size="large-v3-turbo"))

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


@requires_voicecli
@pytest.mark.asyncio
async def test_transcribe_none_language_becomes_unknown():
    svc = STTService(STTConfig(model_size="large-v3-turbo"))

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


@requires_voicecli
@pytest.mark.asyncio
async def test_transcribe_empty_segments_duration_zero():
    svc = STTService(STTConfig(model_size="large-v3-turbo"))

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


@requires_voicecli
@pytest.mark.asyncio
async def test_transcribe_propagates_error():
    svc = STTService(STTConfig(model_size="large-v3-turbo"))

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


# ---------------------------------------------------------------------------
# S3 — T13: STTConfig detection fields and _transcribe_sync forwarding
# ---------------------------------------------------------------------------


def test_stt_config_has_detection_fields():
    """STTConfig gains three optional language detection parameters."""
    cfg = STTConfig(model_size="large-v3-turbo")
    assert cfg.language_detection_threshold is None
    assert cfg.language_detection_segments is None
    assert cfg.language_fallback is None


def test_stt_config_accepts_detection_fields():
    """All three detection params can be set."""
    cfg = STTConfig(
        model_size="large-v3-turbo",
        language_detection_threshold=0.90,
        language_detection_segments=3,
        language_fallback="en",
    )
    assert cfg.language_detection_threshold == 0.90
    assert cfg.language_detection_segments == 3
    assert cfg.language_fallback == "en"


@requires_voicecli
def test_transcribe_sync_passes_detection_threshold():
    """_transcribe_sync passes non-None detection params to _transcribe()."""
    cfg = STTConfig(
        model_size="large-v3-turbo",
        language_detection_threshold=0.90,
        language_fallback="en",
    )
    svc = STTService(cfg)

    captured_kwargs: dict = {}

    def fake_transcribe(path, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_vc_result(text="bonjour", language="fr")

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", side_effect=fake_transcribe),
    ):
        svc._transcribe_sync("/fake/path.ogg")

    assert captured_kwargs.get("language_detection_threshold") == 0.90
    assert captured_kwargs.get("language_fallback") == "en"
    # None value → not passed at all
    assert "language_detection_segments" not in captured_kwargs


@requires_voicecli
def test_transcribe_sync_no_detection_params_when_none():
    """_transcribe_sync does not pass detection params when all are None."""
    cfg = STTConfig(model_size="large-v3-turbo")
    svc = STTService(cfg)

    captured_kwargs: dict = {}

    def fake_transcribe(path, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_vc_result(text="hello", language="en")

    with (
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", side_effect=fake_transcribe),
    ):
        svc._transcribe_sync("/fake/path.ogg")

    assert "language_detection_threshold" not in captured_kwargs
    assert "language_detection_segments" not in captured_kwargs
    assert "language_fallback" not in captured_kwargs


# ---------------------------------------------------------------------------
# Daemon detection state machine (per-call socket check)
# ---------------------------------------------------------------------------

_SOCKET_PATCH = "voicecli.stt_daemon.SOCKET_PATH"
_UNLOAD_PATCH = "voicecli.transcribe.unload_model"


def _stt_service() -> STTService:
    return STTService(STTConfig(model_size="large-v3-turbo"))


def _mock_socket(exists: bool) -> MagicMock:
    """Return a mock Path whose .exists() returns the given value."""
    sock = MagicMock(spec=Path)
    sock.exists.return_value = exists
    return sock


@requires_voicecli
def test_daemon_appears_unloads_model():
    """Transition 1: daemon_up=True, _daemon_active=False → unload + activate."""
    svc = _stt_service()
    assert svc._daemon_active is False

    with (
        patch(_SOCKET_PATCH, _mock_socket(True)),
        patch(_UNLOAD_PATCH) as mock_unload,
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", return_value=_make_vc_result()),
    ):
        svc._transcribe_sync("/tmp/fake.ogg")

    mock_unload.assert_called_once()
    assert svc._daemon_active is True


@requires_voicecli
def test_daemon_disappears_falls_back():
    """Transition 2: daemon_up=False, _daemon_active=True → reset + warn."""
    svc = _stt_service()
    svc._daemon_active = True

    with (
        patch(_SOCKET_PATCH, _mock_socket(False)),
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", return_value=_make_vc_result()),
    ):
        svc._transcribe_sync("/tmp/fake.ogg")

    assert svc._daemon_active is False


@requires_voicecli
def test_daemon_already_active_no_op():
    """Transition 3: daemon_up=True, _daemon_active=True → no unload call."""
    svc = _stt_service()
    svc._daemon_active = True

    with (
        patch(_SOCKET_PATCH, _mock_socket(True)),
        patch(_UNLOAD_PATCH) as mock_unload,
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", return_value=_make_vc_result()),
    ):
        svc._transcribe_sync("/tmp/fake.ogg")

    mock_unload.assert_not_called()
    assert svc._daemon_active is True


@requires_voicecli
def test_no_daemon_no_flag_no_op():
    """Transition 4: daemon_up=False, _daemon_active=False → no state change."""
    svc = _stt_service()

    with (
        patch(_SOCKET_PATCH, _mock_socket(False)),
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch("voicecli.transcribe.transcribe", return_value=_make_vc_result()),
    ):
        svc._transcribe_sync("/tmp/fake.ogg")

    assert svc._daemon_active is False


@requires_voicecli
def test_daemon_connection_fails_retries_in_process():
    """TOCTOU recovery: daemon socket exists but connection fails → retry in-process."""
    svc = _stt_service()
    svc._daemon_active = True

    call_count = 0

    def _transcribe_with_failure(path, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("daemon socket gone")
        return _make_vc_result()

    with (
        patch(_SOCKET_PATCH, _mock_socket(True)),
        patch("voicecli.config.load_vocab", return_value=[]),
        patch("voicecli.config.vocab_to_prompt", return_value=""),
        patch(
            "voicecli.transcribe.transcribe",
            side_effect=_transcribe_with_failure,
        ),
    ):
        result = svc._transcribe_sync("/tmp/fake.ogg")

    assert call_count == 2
    assert svc._daemon_active is False
    assert result.text == "Hello world"
