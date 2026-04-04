"""Unit tests for lyra.nats.connect — nats_connect() and _read_nkey_seed().

Mocks nats.connect so no real NATS server is required.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lyra.nats.connect import nats_connect


class TestNatsConnect:
    async def test_connect_with_seed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """nats.connect is called with nkeys_seed_str when seed file exists."""
        # Arrange
        seed_content = "SUAIBKIBKIB123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text(seed_content)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        mock_nc = AsyncMock()
        with patch("lyra.nats.connect.nats.connect", new=AsyncMock(return_value=mock_nc)) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert
            mock_connect.assert_called_once_with(
                "nats://localhost:4222",
                nkeys_seed_str=seed_content,
            )
            assert result is mock_nc

    async def test_connect_without_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """nats.connect is called without nkeys_seed_str when env var is absent."""
        # Arrange
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        mock_nc = AsyncMock()
        with patch("lyra.nats.connect.nats.connect", new=AsyncMock(return_value=mock_nc)) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert — nkeys_seed_str must NOT be present in the call
            mock_connect.assert_called_once_with("nats://localhost:4222")
            assert "nkeys_seed_str" not in mock_connect.call_args.kwargs
            assert result is mock_nc

    async def test_connect_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit is raised when NATS_NKEY_SEED_PATH points to a non-existent file."""
        # Arrange
        missing = tmp_path / "does_not_exist.seed"
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(missing))

        # Act / Assert
        with pytest.raises(SystemExit):
            await nats_connect("nats://localhost:4222")

    async def test_connect_directory_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit is raised when NATS_NKEY_SEED_PATH points to a directory."""
        # Arrange — tmp_path itself is a directory
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(tmp_path))

        # Act / Assert
        with pytest.raises(SystemExit):
            await nats_connect("nats://localhost:4222")
