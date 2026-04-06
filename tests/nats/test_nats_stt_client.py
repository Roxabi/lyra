"""Tests for NatsSttClient timeout resolution logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lyra.nats.nats_stt_client import NatsSttClient


@pytest.fixture()
def mock_nc() -> MagicMock:
    return MagicMock()


class TestTimeoutResolution:
    def test_default_timeout_is_15s(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LYRA_STT_TIMEOUT", raising=False)
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0

    def test_env_var_overrides_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "30")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 30.0

    def test_explicit_timeout_wins_over_env_var(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "99")
        client = NatsSttClient(nc=mock_nc, timeout=5.0)
        assert client._timeout == 5.0

    def test_malformed_env_var_falls_back_to_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "not-a-number")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0

    def test_empty_env_var_falls_back_to_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0
