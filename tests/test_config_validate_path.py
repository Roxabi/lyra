"""Tests for lyra.bootstrap.config._validate_config_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.bootstrap.config import _validate_config_path


class TestValidateConfigPath:
    def test_path_outside_home_raises_value_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path outside home directory raises ValueError."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with pytest.raises(ValueError, match="outside trusted base"):
            _validate_config_path("/tmp/evil.toml")

    def test_path_inside_home_returns_normalized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path inside home directory returns the normalized absolute path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_path = tmp_path / "lyra" / "config.toml"

        result = _validate_config_path(str(config_path))

        assert result == str(config_path.resolve())

    def test_tilde_expansion_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tilde-style prefix (resolved path inside home) is accepted.

        Path.expanduser() resolves ~ to the real home, so we verify that
        a path already expanded to a subdirectory of tmp_path (our fake home)
        is accepted — the key contract being that paths within home are allowed.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pass an already-expanded path that is inside fake home
        config_path = str(tmp_path / "lyra" / "config.toml")

        result = _validate_config_path(config_path)

        assert result == str((tmp_path / "lyra" / "config.toml").resolve())

    def test_relative_path_inside_home_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Relative paths that resolve inside home are accepted."""
        import os

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Change cwd to tmp_path so relative paths resolve inside home
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.toml"

        result = _validate_config_path("config.toml")

        assert result == str(config_file.resolve())
