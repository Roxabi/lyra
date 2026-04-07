"""Tests for voice_overlay bootstrap helpers."""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.bootstrap.voice_overlay import (
    init_nats_stt,
    init_nats_tts,
    probe_voice_services,
)
from lyra.nats.nats_stt_client import NatsSttClient
from lyra.nats.nats_tts_client import NatsTtsClient


@pytest.fixture()
def mock_nc() -> MagicMock:
    return MagicMock()


class TestInitNatsStt:
    def test_stt_enabled_returns_client_with_model(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_STT_ENABLED", "1")
        monkeypatch.setenv("LYRA_STT_MODEL", "tiny")
        monkeypatch.delenv("STT_MODEL_SIZE", raising=False)
        # Act
        client = init_nats_stt(mock_nc)
        # Assert
        assert isinstance(client, NatsSttClient)
        assert client._model == "tiny"

    def test_stt_disabled_returns_none(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("LYRA_STT_ENABLED", raising=False)
        # Act
        client = init_nats_stt(mock_nc)
        # Assert
        assert client is None

    def test_stt_explicit_zero_returns_none(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_STT_ENABLED", "0")
        # Act
        client = init_nats_stt(mock_nc)
        # Assert
        assert client is None

    def test_stt_deprecated_fallback_emits_warning(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_STT_ENABLED", "1")
        monkeypatch.delenv("LYRA_STT_MODEL", raising=False)
        monkeypatch.setenv("STT_MODEL_SIZE", "medium")
        # Act
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = init_nats_stt(mock_nc)
        # Assert
        assert isinstance(client, NatsSttClient)
        assert client._model == "medium"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        assert "STT_MODEL_SIZE" in str(deprecation_warnings[0].message)

    def test_stt_new_var_wins_over_deprecated(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — both vars set; LYRA_STT_MODEL should win
        monkeypatch.setenv("LYRA_STT_ENABLED", "1")
        monkeypatch.setenv("LYRA_STT_MODEL", "tiny")
        monkeypatch.setenv("STT_MODEL_SIZE", "large")
        # Act
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = init_nats_stt(mock_nc)
        # Assert
        assert isinstance(client, NatsSttClient)
        assert client._model == "tiny"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings == []


class TestInitNatsTts:
    def test_tts_enabled_returns_client(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_TTS_ENABLED", "1")
        # Act
        client = init_nats_tts(mock_nc)
        # Assert
        assert isinstance(client, NatsTtsClient)

    def test_tts_disabled_returns_none(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("LYRA_TTS_ENABLED", raising=False)
        # Act
        client = init_nats_tts(mock_nc)
        # Assert
        assert client is None

    def test_tts_independent_of_stt(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — TTS enabled, STT not enabled
        monkeypatch.setenv("LYRA_TTS_ENABLED", "1")
        monkeypatch.delenv("LYRA_STT_ENABLED", raising=False)
        # Act
        tts_client = init_nats_tts(mock_nc)
        stt_client = init_nats_stt(mock_nc)
        # Assert — TTS returned regardless of STT state
        assert isinstance(tts_client, NatsTtsClient)
        assert stt_client is None


class TestProbeVoiceServices:
    @pytest.mark.asyncio
    async def test_stt_unreachable_logs_warning_no_raise(self) -> None:
        # Arrange
        from nats.errors import NoRespondersError

        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=NoRespondersError())
        fake_stt = MagicMock()
        # Act / Assert — should not raise
        await probe_voice_services(mock_nc, stt=fake_stt, tts=None)

    @pytest.mark.asyncio
    async def test_tts_unreachable_logs_warning_no_raise(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        fake_tts = MagicMock()
        # Act / Assert — should not raise
        await probe_voice_services(mock_nc, stt=None, tts=fake_tts)

    @pytest.mark.asyncio
    async def test_skip_none_clients_no_request_called(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        # Act
        await probe_voice_services(mock_nc, stt=None, tts=None)
        # Assert — nc.request never called when both clients are None
        mock_nc.request.assert_not_called()
