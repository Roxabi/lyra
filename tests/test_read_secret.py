"""Tests for lyra.bootstrap.infra.health.Secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.bootstrap.infra.health import Secrets


class TestSecrets:
    def _setup_secrets_dir(self, tmp_path: Path) -> Path:
        """Create tmp_path/secrets/ and return tmp_path as vault_dir."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        return tmp_path

    def test_file_exists_returns_stripped_content(self, tmp_path: Path) -> None:
        """File with content returns the stripped string."""
        vault_dir = self._setup_secrets_dir(tmp_path)
        (vault_dir / "secrets" / "my_secret").write_text("supersecret")

        result = Secrets(vault_dir=vault_dir)._read("my_secret")

        assert result == "supersecret"

    def test_trailing_whitespace_and_newline_stripped(self, tmp_path: Path) -> None:
        """Trailing whitespace and newlines are stripped."""
        vault_dir = self._setup_secrets_dir(tmp_path)
        (vault_dir / "secrets" / "token").write_text("  my-token\n  ")

        result = Secrets(vault_dir=vault_dir)._read("token")

        assert result == "my-token"

    def test_missing_file_returns_empty_string(self, tmp_path: Path) -> None:
        """Missing secret file returns empty string (no exception)."""
        vault_dir = self._setup_secrets_dir(tmp_path)

        result = Secrets(vault_dir=vault_dir)._read("nonexistent")

        assert result == ""

    def test_empty_file_returns_empty_string(self, tmp_path: Path) -> None:
        """Empty secret file returns empty string."""
        vault_dir = self._setup_secrets_dir(tmp_path)
        (vault_dir / "secrets" / "empty_secret").write_text("")

        result = Secrets(vault_dir=vault_dir)._read("empty_secret")

        assert result == ""

    def test_permission_error_returns_empty_string(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """PermissionError (OSError) returns empty string instead of propagating."""
        import logging

        vault_dir = self._setup_secrets_dir(tmp_path)
        secret_file = vault_dir / "secrets" / "locked_secret"
        secret_file.write_text("private")
        secret_file.chmod(0o000)

        try:
            with caplog.at_level(logging.WARNING, logger="lyra.bootstrap.infra.health"):
                result = Secrets(vault_dir=vault_dir)._read("locked_secret")
            assert result == ""
            assert "Could not read secret" in caplog.text
        finally:
            # Restore permissions so tmp_path cleanup works
            secret_file.chmod(0o644)

    def test_health_secret_cached_property(self, tmp_path: Path) -> None:
        """health_secret cached_property reads from secrets/health_secret."""
        vault_dir = self._setup_secrets_dir(tmp_path)
        (vault_dir / "secrets" / "health_secret").write_text("my-health-token\n")

        secrets = Secrets(vault_dir=vault_dir)
        result = secrets.health_secret

        assert result == "my-health-token"
        # Second access returns same value (cached_property)
        assert secrets.health_secret == "my-health-token"
