"""Tests for lyra setup commands CLI (#291).

Covers:
  - _register_all() with mocked CredentialStore and Bot
  - Admin commands excluded from registration
  - Missing token handled gracefully
  - Idempotent re-run
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        textwrap.dedent("""\
            [telegram]
            [[telegram.bots]]
            bot_id = "test_bot"
            agent = "default"
        """)
    )
    return config


@pytest.fixture()
def empty_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text("[telegram]\n")
    return config


def _mock_patches():
    """Return context managers for all external dependencies."""
    mock_cred_store = AsyncMock()
    mock_cred_store.get_full.return_value = ("fake_token", "fake_secret")

    mock_keyring = MagicMock()

    mock_plugin_loader = MagicMock()
    mock_plugin_loader.get_command_descriptions.return_value = {}

    return mock_cred_store, mock_keyring, mock_plugin_loader


class TestRegisterAll:
    @pytest.mark.asyncio()
    async def test_registers_commands(self, config_file: Path) -> None:
        from lyra.cli_setup import _register_all

        mock_cred_store, mock_keyring, mock_plugin_loader = _mock_patches()

        mock_register = AsyncMock(return_value="test_bot_user")

        with (
            patch(
                "lyra.core.credential_store.LyraKeyring.load_or_create",
                return_value=mock_keyring,
            ),
            patch(
                "lyra.core.credential_store.CredentialStore",
                return_value=mock_cred_store,
            ),
            patch(
                "lyra.core.command_loader.CommandLoader",
                return_value=mock_plugin_loader,
            ),
            patch("lyra.cli_setup._register_telegram_bot", mock_register),
        ):
            await _register_all(str(config_file))

        mock_register.assert_called_once()
        _, _, public_commands = mock_register.call_args[0]
        cmd_names = [c.name for c in public_commands]
        # Admin commands excluded
        assert "/circuit" not in cmd_names
        assert "/routing" not in cmd_names
        assert "/config" not in cmd_names
        # Public commands included
        assert "/help" in cmd_names
        assert "/stop" in cmd_names
        assert "/join" in cmd_names
        assert "/leave" in cmd_names

    @pytest.mark.asyncio()
    async def test_no_bots_configured(self, empty_config: Path) -> None:
        from lyra.cli_setup import _register_all

        # Should not raise — prints message and returns
        with (
            patch(
                "lyra.core.credential_store.LyraKeyring.load_or_create",
                return_value=MagicMock(),
            ),
            patch(
                "lyra.core.credential_store.CredentialStore", return_value=AsyncMock()
            ),
            patch("lyra.core.command_loader.CommandLoader", return_value=MagicMock()),
        ):
            await _register_all(str(empty_config))

    @pytest.mark.asyncio()
    async def test_missing_credentials(self, config_file: Path) -> None:
        from click.exceptions import Exit as ClickExit

        from lyra.cli_setup import _register_all

        mock_cred_store = AsyncMock()
        mock_cred_store.get_full.return_value = None

        with (
            patch(
                "lyra.core.credential_store.LyraKeyring.load_or_create",
                return_value=MagicMock(),
            ),
            patch(
                "lyra.core.credential_store.CredentialStore",
                return_value=mock_cred_store,
            ),
            patch(
                "lyra.core.command_loader.CommandLoader",
                return_value=MagicMock(
                    get_command_descriptions=MagicMock(return_value={})
                ),
            ),
            pytest.raises((SystemExit, ClickExit)),
        ):
            await _register_all(str(config_file))

    @pytest.mark.asyncio()
    async def test_idempotent_rerun(self, config_file: Path) -> None:
        from lyra.cli_setup import _register_all

        mock_cred_store, mock_keyring, mock_plugin_loader = _mock_patches()
        mock_register = AsyncMock(return_value="test_bot_user")

        with (
            patch(
                "lyra.core.credential_store.LyraKeyring.load_or_create",
                return_value=mock_keyring,
            ),
            patch(
                "lyra.core.credential_store.CredentialStore",
                return_value=mock_cred_store,
            ),
            patch(
                "lyra.core.command_loader.CommandLoader",
                return_value=mock_plugin_loader,
            ),
            patch("lyra.cli_setup._register_telegram_bot", mock_register),
        ):
            await _register_all(str(config_file))
            await _register_all(str(config_file))

        assert mock_register.call_count == 2

    @pytest.mark.asyncio()
    async def test_config_not_found(self, tmp_path: Path) -> None:
        from click.exceptions import Exit as ClickExit

        from lyra.cli_setup import _register_all

        with pytest.raises((SystemExit, ClickExit)):
            await _register_all(str(tmp_path / "nonexistent.toml"))
