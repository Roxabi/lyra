"""Tests for `lyra bot` CLI commands (issue #262, S3).

Uses typer.testing.CliRunner so no real TTY or subprocess is required.
CredentialStore is backed by a real tmp_path SQLite DB (no storage mocks) —
only `_open_credential_store` and `typer.confirm` are patched.

NOTE: The CLI commands always close the store in their `finally` block.
When patching `_open_credential_store` the same store object is passed in,
so after the command returns the store is closed.  For post-command
verification we open a fresh store against the same db/key files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lyra.cli_bot import bot_app
from lyra.core.credential_store import CredentialStore, LyraKeyring

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


async def _make_store(tmp_path: Path) -> CredentialStore:
    """Create and connect a real CredentialStore backed by a tmp file DB."""
    key_path = tmp_path / "lyra.key"
    keyring = await LyraKeyring.load_or_create(key_path)
    store = CredentialStore(db_path=str(tmp_path / "auth.db"), keyring=keyring)
    await store.connect()
    return store


async def _fresh_read(tmp_path: Path, platform: str, bot_id: str) -> str | None:
    """Open a fresh store connection and read a token, then close it.

    Use this for post-command assertions because the CLI closes the store it
    receives in its finally block.
    """
    store = await _make_store(tmp_path)
    try:
        return await store.get(platform, bot_id)
    finally:
        await store.close()


async def _fresh_get_full(
    tmp_path: Path, platform: str, bot_id: str
) -> tuple[str, str | None] | None:
    """Open a fresh store connection and call get_full, then close it."""
    store = await _make_store(tmp_path)
    try:
        return await store.get_full(platform, bot_id)
    finally:
        await store.close()


def _assert_ok(result: object, *, label: str = "") -> None:
    """Assert exit_code == 0 with a readable failure message."""
    from click.testing import Result  # local import for type only

    r: Result = result  # type: ignore[assignment]
    prefix = f"[{label}] " if label else ""
    assert r.exit_code == 0, (
        f"{prefix}Expected exit 0, got {r.exit_code}:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# TestBotAdd
# ---------------------------------------------------------------------------


class TestBotAdd:
    """lyra bot add — stores credentials, handles overwrites, validates platform."""

    def test_bot_add_stores_credential(self, tmp_path: Path) -> None:
        """Invoking bot add stores the token in the credential store."""
        # Arrange — pre-create key so both store instances share it
        store = asyncio.run(_make_store(tmp_path))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform", "telegram",
                    "--bot-id", "test",
                    "--token", "abc123",
                ],
            )

        # Assert output
        _assert_ok(result, label="bot add")
        assert "test" in result.output or "stored" in result.output.lower()

        # Verify token landed in the DB (fresh conn — CLI closed the store)
        token = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert token == "abc123"

    def test_bot_add_overwrite_confirmed_stores_new_token(
        self, tmp_path: Path
    ) -> None:
        """When credential exists and user confirms, new token is stored."""
        # Arrange — seed an existing credential
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "test", "old-token"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            with patch(
                "lyra.cli_bot.typer.confirm", return_value=True
            ) as mock_confirm:
                # Act
                result = runner.invoke(
                    bot_app,
                    [
                        "add",
                        "--platform", "telegram",
                        "--bot-id", "test",
                        "--token", "new-token",
                    ],
                )

        # Assert
        mock_confirm.assert_called_once()
        _assert_ok(result, label="bot add overwrite")
        # Fresh store for verification (CLI closed the patched store)
        stored = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert stored == "new-token"

    def test_bot_add_overwrite_aborted_keeps_old_token(
        self, tmp_path: Path
    ) -> None:
        """When credential exists and user aborts confirm, old token is kept."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "test", "old-token"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # typer.confirm with abort=True raises typer.Abort on False;
            # simulate this by raising Abort directly.
            import typer as _typer

            with patch(
                "lyra.cli_bot.typer.confirm", side_effect=_typer.Abort()
            ):
                # Act
                result = runner.invoke(
                    bot_app,
                    [
                        "add",
                        "--platform", "telegram",
                        "--bot-id", "test",
                        "--token", "new-token",
                    ],
                )

        # Assert — exit code 1 when user aborts
        assert result.exit_code != 0
        # Original token still intact (fresh store — CLI may close patched one)
        stored = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert stored == "old-token"

    def test_bot_add_invalid_platform_exits_nonzero(self, tmp_path: Path) -> None:
        """Providing an unsupported platform prints an error and exits non-zero."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform", "slack",
                    "--bot-id", "test",
                    "--token", "abc",
                ],
            )

        # Assert
        assert result.exit_code != 0
        assert "slack" in result.output or "Unknown platform" in result.output

    def test_bot_add_with_webhook_secret(self, tmp_path: Path) -> None:
        """Webhook secret is stored alongside the token."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform", "telegram",
                    "--bot-id", "main",
                    "--token", "tok123",
                    "--webhook-secret", "mysecret",
                ],
            )

        # Assert
        _assert_ok(result, label="bot add webhook-secret")
        # Fresh store for verification (CLI closed the patched store in finally)
        creds = asyncio.run(_fresh_get_full(tmp_path, "telegram", "main"))
        assert creds is not None
        token, secret = creds
        assert token == "tok123"
        assert secret == "mysecret"


# ---------------------------------------------------------------------------
# TestBotList
# ---------------------------------------------------------------------------


class TestBotList:
    """lyra bot list — shows masked tokens or empty-state message."""

    def test_bot_list_shows_masked_token(self, tmp_path: Path) -> None:
        """Stored credentials appear with the token masked as ***...XXXX."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "main", "super-secret-token"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(bot_app, ["list"])

        # Assert
        _assert_ok(result, label="bot list masked")
        # Masked format: ***...XXXX (last 4 chars of plaintext)
        assert "***..." in result.output
        # Last 4 chars of "super-secret-token"
        assert "oken" in result.output

    def test_bot_list_empty_state(self, tmp_path: Path) -> None:
        """Empty credential store shows the 'No credentials stored' message."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(bot_app, ["list"])

        # Assert
        _assert_ok(result, label="bot list empty")
        assert "No credentials stored" in result.output

    def test_bot_list_multiple_bots(self, tmp_path: Path) -> None:
        """Multiple bots are all listed, each with a masked token."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "bot-a", "token-aaa"))
        asyncio.run(store.set("discord", "bot-b", "token-bbb"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            # Act
            result = runner.invoke(bot_app, ["list"])

        # Assert
        assert result.exit_code == 0
        assert "telegram" in result.output
        assert "discord" in result.output
        assert "bot-a" in result.output
        assert "bot-b" in result.output

    def test_bot_list_shows_header(self, tmp_path: Path) -> None:
        """The list output includes PLATFORM, BOT ID, TOKEN column headers."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "main", "some-token-xyz"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            result = runner.invoke(bot_app, ["list"])

        # Assert
        assert "PLATFORM" in result.output
        assert "BOT ID" in result.output
        assert "TOKEN" in result.output


# ---------------------------------------------------------------------------
# TestBotRemove
# ---------------------------------------------------------------------------


class TestBotRemove:
    """lyra bot remove — deletes existing credential or reports not-found."""

    def test_bot_remove_existing(self, tmp_path: Path) -> None:
        """Removing an existing credential exits 0 and prints 'Removed'."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "main", "some-token"))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            with patch("lyra.cli_bot.typer.confirm", return_value=True):
                # Act
                result = runner.invoke(
                    bot_app,
                    ["remove", "--platform", "telegram", "--bot-id", "main"],
                )

        # Assert
        _assert_ok(result, label="bot remove existing")
        assert "Removed" in result.output or "removed" in result.output.lower()

        # Verify credential is gone (fresh store — CLI closed the patched one)
        token = asyncio.run(_fresh_read(tmp_path, "telegram", "main"))
        assert token is None

    def test_bot_remove_nonexistent(self, tmp_path: Path) -> None:
        """Removing a non-existent bot prints 'Not found'."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            with patch("lyra.cli_bot.typer.confirm", return_value=True):
                # Act
                result = runner.invoke(
                    bot_app,
                    ["remove", "--platform", "telegram", "--bot-id", "ghost"],
                )

        # Assert
        _assert_ok(result, label="bot remove nonexistent")
        assert "Not found" in result.output or "not found" in result.output.lower()

    def test_bot_remove_aborted_keeps_credential(self, tmp_path: Path) -> None:
        """When user aborts the confirm prompt, credential is not deleted."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("telegram", "main", "keep-me"))

        import typer as _typer

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            with patch(
                "lyra.cli_bot.typer.confirm", side_effect=_typer.Abort()
            ):
                # Act
                result = runner.invoke(
                    bot_app,
                    ["remove", "--platform", "telegram", "--bot-id", "main"],
                )

        # Assert — non-zero exit when aborted
        assert result.exit_code != 0
        # Token must still be present (fresh store — CLI may close on abort)
        token = asyncio.run(_fresh_read(tmp_path, "telegram", "main"))
        assert token == "keep-me"

    def test_bot_remove_confirm_called_with_platform_and_bot_id(
        self, tmp_path: Path
    ) -> None:
        """The confirm prompt message includes the platform and bot_id."""
        # Arrange
        store = asyncio.run(_make_store(tmp_path))
        asyncio.run(store.set("discord", "prod-bot", "dc-token"))

        prompt_messages: list[str] = []

        def _recording_confirm(msg: str, **kwargs: object) -> bool:
            prompt_messages.append(msg)
            return True

        with patch("lyra.cli_bot._open_credential_store", return_value=store):
            with patch(
                "lyra.cli_bot.typer.confirm", side_effect=_recording_confirm
            ):
                runner.invoke(
                    bot_app,
                    [
                        "remove",
                        "--platform", "discord",
                        "--bot-id", "prod-bot",
                    ],
                )

        # Assert
        assert len(prompt_messages) == 1
        assert "discord" in prompt_messages[0]
        assert "prod-bot" in prompt_messages[0]
