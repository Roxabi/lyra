"""Tests for lyra.bootstrap.config._validate_config_path and _load_raw_config."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.bootstrap.config import _load_raw_config, _validate_config_path


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

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Change cwd to tmp_path so relative paths resolve inside home
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.toml"

        result = _validate_config_path("config.toml")

        assert result == str(config_file.resolve())


# ---------------------------------------------------------------------------
# TestLoadRawConfig — resolution order
# ---------------------------------------------------------------------------


def _write_toml(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


class TestLoadRawConfig:
    def test_explicit_path_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit config_path argument takes precedence over all env vars."""
        explicit = tmp_path / "explicit.toml"
        _write_toml(explicit, '[test]\nkey = "explicit"')
        vault = tmp_path / "vault"
        _write_toml(vault / "config.toml", '[test]\nkey = "vault"')
        monkeypatch.setenv("LYRA_VAULT_DIR", str(vault))

        result = _load_raw_config(str(explicit))

        assert result["test"]["key"] == "explicit"

    def test_lyra_config_env_wins_over_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$LYRA_CONFIG takes precedence over $LYRA_VAULT_DIR/config.toml."""
        env_cfg = tmp_path / "env.toml"
        _write_toml(env_cfg, '[test]\nkey = "env"')
        vault = tmp_path / "vault"
        _write_toml(vault / "config.toml", '[test]\nkey = "vault"')
        monkeypatch.setenv("LYRA_CONFIG", str(env_cfg))
        monkeypatch.setenv("LYRA_VAULT_DIR", str(vault))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = _load_raw_config()

        assert result["test"]["key"] == "env"

    def test_vault_dir_config_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$LYRA_VAULT_DIR/config.toml loaded when no explicit path or $LYRA_CONFIG."""
        vault = tmp_path / ".lyra"
        _write_toml(vault / "config.toml", '[test]\nkey = "vault"')
        monkeypatch.setenv("LYRA_VAULT_DIR", str(vault))
        monkeypatch.delenv("LYRA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # no config.toml in cwd

        result = _load_raw_config()

        assert result["test"]["key"] == "vault"

    def test_cwd_fallback_when_vault_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to cwd/config.toml when $LYRA_VAULT_DIR/config.toml is absent."""
        vault = tmp_path / ".lyra"
        vault.mkdir()  # exists but no config.toml inside
        cwd_cfg = tmp_path / "config.toml"
        _write_toml(cwd_cfg, '[test]\nkey = "cwd"')
        monkeypatch.setenv("LYRA_VAULT_DIR", str(vault))
        monkeypatch.delenv("LYRA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        result = _load_raw_config()

        assert result["test"]["key"] == "cwd"

    def test_empty_dict_when_no_config_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns empty dict when no config file exists anywhere."""
        vault = tmp_path / ".lyra"
        vault.mkdir()
        monkeypatch.setenv("LYRA_VAULT_DIR", str(vault))
        monkeypatch.delenv("LYRA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        result = _load_raw_config()

        assert result == {}
