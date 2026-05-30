"""Tests for voice_overlay bootstrap helpers."""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.bootstrap.factory.voice_overlay import (
    init_blobstore,
    init_nats_image,
    init_nats_stt,
    init_nats_tts,
    probe_voice_services,
)
from lyra.nats.nats_image_client import NatsImageClient
from lyra.nats.nats_stt_client import NatsSttClient
from lyra.nats.nats_tts_client import NatsTtsClient


@pytest.fixture()
def mock_nc() -> MagicMock:
    return MagicMock()


class TestInitBlobstore:
    def test_returns_none_when_token_file_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init_blobstore must not raise when token file is missing — returns None."""
        monkeypatch.setenv(
            "LYRA_BLOBSTORE_TOKEN_PATH", "/nonexistent/path/blobstore.tok"
        )
        result = init_blobstore()
        assert result is None

    def test_returns_port_when_token_file_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """init_blobstore returns a BlobStorePort when token file exists."""
        from lyra.core.ports.blobstore import BlobStorePort

        tok = tmp_path / "blobstore.tok"  # type: ignore[operator]
        tok.write_text("test-token")
        monkeypatch.setenv("LYRA_BLOBSTORE_TOKEN_PATH", str(tok))
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://localhost:8449")
        result = init_blobstore()
        assert result is not None
        assert isinstance(result, BlobStorePort)


class TestInitNatsStt:
    def test_returns_client_with_model(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_MODEL", "tiny")
        monkeypatch.delenv("STT_MODEL_SIZE", raising=False)
        client = init_nats_stt(mock_nc)
        assert isinstance(client, NatsSttClient)
        assert client._model == "tiny"

    def test_always_returns_client(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init_nats_stt always returns a NatsSttClient — no flag gate."""
        monkeypatch.delenv("LYRA_STT_ENABLED", raising=False)
        monkeypatch.delenv("LYRA_STT_MODEL", raising=False)
        monkeypatch.delenv("STT_MODEL_SIZE", raising=False)
        client = init_nats_stt(mock_nc)
        assert isinstance(client, NatsSttClient)

    def test_deprecated_fallback_emits_warning(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LYRA_STT_MODEL", raising=False)
        monkeypatch.setenv("STT_MODEL_SIZE", "medium")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = init_nats_stt(mock_nc)
        assert isinstance(client, NatsSttClient)
        assert client._model == "medium"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
        assert "STT_MODEL_SIZE" in str(deprecation_warnings[0].message)

    def test_new_var_wins_over_deprecated(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_MODEL", "tiny")
        monkeypatch.setenv("STT_MODEL_SIZE", "large")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = init_nats_stt(mock_nc)
        assert isinstance(client, NatsSttClient)
        assert client._model == "tiny"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings == []


class TestInitNatsTts:
    def test_returns_client(self, mock_nc: MagicMock) -> None:
        client = init_nats_tts(mock_nc)
        assert isinstance(client, NatsTtsClient)

    def test_always_returns_client(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init_nats_tts always returns a NatsTtsClient — no flag gate."""
        monkeypatch.delenv("LYRA_TTS_ENABLED", raising=False)
        client = init_nats_tts(mock_nc)
        assert isinstance(client, NatsTtsClient)


class TestInitNatsImage:
    def test_returns_client(self, mock_nc: MagicMock) -> None:
        client = init_nats_image(mock_nc)
        assert isinstance(client, NatsImageClient)


class TestProbeVoiceServices:
    @pytest.mark.asyncio
    async def test_stt_unreachable_logs_warning_no_raise(self) -> None:
        from nats.errors import NoRespondersError

        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=NoRespondersError())
        fake_stt = MagicMock()
        await probe_voice_services(mock_nc, stt=fake_stt, tts=None)

    @pytest.mark.asyncio
    async def test_tts_unreachable_logs_warning_no_raise(self) -> None:
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        fake_tts = MagicMock()
        await probe_voice_services(mock_nc, stt=None, tts=fake_tts)

    @pytest.mark.asyncio
    async def test_skip_none_clients_no_request_called(self) -> None:
        mock_nc = AsyncMock()
        await probe_voice_services(mock_nc, stt=None, tts=None)
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_generic_exception_is_silently_swallowed(self) -> None:
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=RuntimeError("boom"))
        fake_stt = MagicMock()
        await probe_voice_services(mock_nc, stt=fake_stt, tts=None)
        mock_nc.request.assert_called_once()
