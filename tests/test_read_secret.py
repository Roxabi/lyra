"""Tests for lyra.bootstrap.health._read_secret."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.bootstrap.health import _read_secret


class TestReadSecret:
    def _setup_secrets_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Set up ~/.lyra/secrets/ structure under tmp_path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        secrets_dir = tmp_path / ".lyra" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        return secrets_dir

    def test_file_exists_returns_stripped_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File with content returns the stripped string."""
        secrets_dir = self._setup_secrets_dir(tmp_path, monkeypatch)
        (secrets_dir / "my_secret").write_text("supersecret")

        result = _read_secret("my_secret")

        assert result == "supersecret"

    def test_trailing_whitespace_and_newline_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trailing whitespace and newlines are stripped."""
        secrets_dir = self._setup_secrets_dir(tmp_path, monkeypatch)
        (secrets_dir / "token").write_text("  my-token\n  ")

        result = _read_secret("token")

        assert result == "my-token"

    def test_missing_file_returns_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing secret file returns empty string (no exception)."""
        self._setup_secrets_dir(tmp_path, monkeypatch)

        result = _read_secret("nonexistent")

        assert result == ""

    def test_empty_file_returns_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty secret file returns empty string."""
        secrets_dir = self._setup_secrets_dir(tmp_path, monkeypatch)
        (secrets_dir / "empty_secret").write_text("")

        result = _read_secret("empty_secret")

        assert result == ""

    def test_permission_error_returns_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError (OSError) returns empty string instead of propagating."""
        secrets_dir = self._setup_secrets_dir(tmp_path, monkeypatch)
        secret_file = secrets_dir / "locked_secret"
        secret_file.write_text("private")
        secret_file.chmod(0o000)

        try:
            result = _read_secret("locked_secret")
            assert result == ""
        finally:
            # Restore permissions so tmp_path cleanup works
            secret_file.chmod(0o644)
