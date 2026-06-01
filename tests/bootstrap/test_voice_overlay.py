"""Tests for voice_overlay bootstrap helpers."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.bootstrap.factory.voice_overlay import (
    init_blobstore,
    init_nats_image,
    init_nats_stt,
    init_nats_tts,
    probe_voice_services,
)
from lyra.nats.audio.nats_tts_client import NatsTtsClient
from lyra.nats.image.nats_image_client import NatsImageClient
from lyra.nats.stt.nats_stt_client import NatsSttClient


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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """init_blobstore returns a BlobStorePort when token file exists."""
        from lyra.core.ports.blobstore import BlobStorePort

        tok = tmp_path / "blobstore.tok"
        tok.write_text("test-token")
        monkeypatch.setenv("LYRA_BLOBSTORE_TOKEN_PATH", str(tok))
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://localhost:8449")
        result = init_blobstore()
        assert result is not None
        assert isinstance(result, BlobStorePort)

    def test_raises_oserror_when_token_file_is_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """init_blobstore raises OSError when token file exists but is empty.

        An empty token file is misconfiguration — the process must not silently
        continue with a blank credential.  The backend fixer adds this raise to
        voice_overlay.init_blobstore(); this test is the contract for that behavior.

        Negative-test: if the empty-token raise is removed from init_blobstore,
        this test will fail (no OSError is raised and pytest.raises catches nothing).
        """
        tok = tmp_path / "blobstore.tok"
        tok.write_text("")  # empty file — token is missing
        monkeypatch.setenv("LYRA_BLOBSTORE_TOKEN_PATH", str(tok))
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://localhost:8449")
        with pytest.raises(OSError):
            init_blobstore()

    def test_raises_oserror_when_token_file_is_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """init_blobstore raises OSError when token file contains only whitespace.

        Whitespace-only content strips to empty string — same misconfiguration as
        an empty file.  The check must run AFTER .strip() so "  \n  " is rejected.
        """
        tok = tmp_path / "blobstore.tok"
        tok.write_text("   \n   ")  # whitespace-only
        monkeypatch.setenv("LYRA_BLOBSTORE_TOKEN_PATH", str(tok))
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://localhost:8449")
        with pytest.raises(OSError):
            init_blobstore()


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


class TestInitBlobstoreLoopbackWarning:
    """Guard: cleartext bearer token on non-loopback URLs must emit a WARNING.

    Negative-test: if the non-loopback http:// guard is deleted from
    init_blobstore, the http://10.0.0.5 case will emit no warning and the
    caplog assertion will fail.
    """

    @pytest.mark.parametrize(
        ("url", "expect_warning"),
        [
            ("http://10.0.0.5:8449", True),  # non-loopback http — must warn
            ("http://[2001:db8::1]:8449", True),  # IPv6 remote — must warn
            ("http://localhost:8449", False),  # loopback hostname — silent
            ("http://127.0.0.1:8449", False),  # loopback IP — silent
            ("http://[::1]:8449", False),  # IPv6 loopback — silent
            ("http://127.1:8449", False),  # IPv4 alias loopback 127.0.0.1 — silent
            ("http://2130706433:8449", False),  # 0x7f000001 decimal — silent
            ("http://0x7f000001:8449", False),  # hex literal loopback — silent
            ("http:///blob", False),  # no host (None) — silent
            ("https://host:8449", False),  # TLS — silent
        ],
        ids=[
            "nonloopback-http",
            "ipv6-remote",
            "localhost",
            "127.0.0.1",
            "ipv6-loopback",
            "127.1-alias",
            "decimal-loopback",
            "hex-loopback",
            "no-host",
            "https",
        ],
    )
    def test_loopback_warning_guard(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: "Path",
        caplog: pytest.LogCaptureFixture,
        url: str,
        expect_warning: bool,
    ) -> None:
        """init_blobstore warns on cleartext bearer token over non-loopback http://."""
        import logging

        tok = tmp_path / "blobstore.tok"
        tok.write_text("test-token")
        monkeypatch.setenv("LYRA_BLOBSTORE_TOKEN_PATH", str(tok))
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", url)

        with caplog.at_level(logging.WARNING):
            result = init_blobstore()

        # Every URL must still produce a non-None adapter (warning-only, no rejection)
        assert result is not None

        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and ("cleartext" in r.getMessage().lower() or url in r.getMessage())
        ]
        if expect_warning:
            assert len(warning_records) >= 1, (
                f"Expected a cleartext-bearer WARNING for URL {url!r}"
                " but none was logged"
            )
        else:
            assert len(warning_records) == 0, (
                f"Expected NO cleartext warning for URL {url!r} but got: "
                + str([r.getMessage() for r in warning_records])
            )


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
