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
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("lyra.nats.connect.nats.connect", new=mock_conn) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["nkeys_seed_str"] == seed_content
            assert "error_cb" in call_kwargs
            assert "disconnected_cb" in call_kwargs
            assert "reconnected_cb" in call_kwargs
            assert result is mock_nc

    async def test_connect_without_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats.connect called without nkeys_seed_str when env var absent."""
        # Arrange
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        mock_nc = AsyncMock()
        mock_conn = AsyncMock(return_value=mock_nc)
        with patch("lyra.nats.connect.nats.connect", new=mock_conn) as mock_connect:
            # Act
            result = await nats_connect("nats://localhost:4222")

            # Assert — nkeys_seed_str must NOT be present in the call
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args.kwargs
            assert "nkeys_seed_str" not in call_kwargs
            assert "error_cb" in call_kwargs
            assert result is mock_nc

    async def test_connect_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_NKEY_SEED_PATH points to missing file."""
        # Arrange
        missing = tmp_path / "does_not_exist.seed"
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(missing))

        # Act / Assert
        with pytest.raises(SystemExit, match="is not a file"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_directory_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when NATS_NKEY_SEED_PATH points to a directory."""
        # Arrange — tmp_path itself is a directory
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(tmp_path))

        # Act / Assert
        with pytest.raises(SystemExit, match="is not a file"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_unreadable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file exists but is unreadable."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("seed-content")
        seed_file.chmod(0o000)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="unreadable"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_empty_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file is empty or whitespace-only."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("   \n  ")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="is empty"):
            await nats_connect("nats://localhost:4222")

    async def test_connect_world_readable_seed_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit when seed file permissions are not 0o600."""
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SU-valid-seed")
        seed_file.chmod(0o644)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Act / Assert
        with pytest.raises(SystemExit, match="unsafe permissions"):
            await nats_connect("nats://localhost:4222")
