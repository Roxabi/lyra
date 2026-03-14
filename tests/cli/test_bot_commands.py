"""Tests for `lyra bot` CLI commands (issue #262, S3).

Uses typer.testing.CliRunner so no real TTY or subprocess is required.
CredentialStore is backed by a real tmp_path SQLite DB (no storage mocks) —
`lyra.cli_bot._make_store` is patched to redirect vault to tmp_path, and
`typer.confirm` is patched where needed.

Each test creates its store fresh (connect + close) in a single asyncio.run()
to avoid cross-event-loop aiosqlite connection issues.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lyra.cli_bot import bot_app
from lyra.core.credential_store import CredentialStore, LyraKeyring

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture(autouse=True)
def restore_event_loop():
    """Restore a fresh event loop after each test.

    asyncio.run() closes the event loop on return. CLI commands call
    asyncio.run() internally, leaving the main thread without a current
    event loop and breaking subsequent tests that call asyncio.get_event_loop().
    """
    yield
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


async def _setup_store(
    tmp_path: Path,
    seeds: list[tuple[str, str, str, str | None]] | None = None,
) -> None:
    """Initialise DB + key in tmp_path, optionally seed credentials.

    Opens and closes the connection within a single event loop so that
    subsequent asyncio.run() calls (CLI commands) start with a clean slate.
    """
    key_path = tmp_path / "keyring.key"
    keyring = LyraKeyring.load_or_create(key_path)
    store = CredentialStore(db_path=str(tmp_path / "auth.db"), keyring=keyring)
    await store.connect()
    try:
        for platform, bot_id, token, webhook in seeds or []:
            await store.set(platform, bot_id, token, webhook)
    finally:
        await store.close()


@contextmanager
def _patch_vault(tmp_path: Path):
    """Redirect lyra.cli_bot._make_store to create stores inside tmp_path.

    Each CLI command that calls _make_store gets a fresh connection in its own
    event loop — avoiding the cross-loop aiosqlite issue.
    """

    async def _side_effect(vault: Path) -> CredentialStore:
        key_path = tmp_path / "keyring.key"
        keyring = LyraKeyring.load_or_create(key_path)
        store = CredentialStore(db_path=str(tmp_path / "auth.db"), keyring=keyring)
        await store.connect()
        return store

    with patch("lyra.cli_bot._make_store", side_effect=_side_effect):
        yield


async def _fresh_read(tmp_path: Path, platform: str, bot_id: str) -> str | None:
    """Open a fresh store and return the decrypted token, then close."""
    key_path = tmp_path / "keyring.key"
    keyring = LyraKeyring.load_or_create(key_path)
    store = CredentialStore(db_path=str(tmp_path / "auth.db"), keyring=keyring)
    await store.connect()
    try:
        return await store.get(platform, bot_id)
    finally:
        await store.close()


async def _fresh_get_full(
    tmp_path: Path, platform: str, bot_id: str
) -> tuple[str, str | None] | None:
    """Open a fresh store and return (token, webhook_secret), then close."""
    key_path = tmp_path / "keyring.key"
    keyring = LyraKeyring.load_or_create(key_path)
    store = CredentialStore(db_path=str(tmp_path / "auth.db"), keyring=keyring)
    await store.connect()
    try:
        return await store.get_full(platform, bot_id)
    finally:
        await store.close()


def _assert_ok(result: object, *, label: str = "") -> None:
    """Assert exit_code == 0 with a readable failure message."""
    from click.testing import Result  # local import for type only

    r: Result = result  # type: ignore[assignment]
    prefix = f"[{label}] " if label else ""
    assert r.exit_code == 0, f"{prefix}Expected exit 0, got {r.exit_code}:\n{r.output}"


# ---------------------------------------------------------------------------
# TestBotAdd
# ---------------------------------------------------------------------------


class TestBotAdd:
    """lyra bot add — stores credentials, handles overwrites, validates platform."""

    def test_bot_add_stores_credential(self, tmp_path: Path) -> None:
        """Invoking bot add stores the token in the credential store."""
        asyncio.run(_setup_store(tmp_path))

        with _patch_vault(tmp_path):
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform",
                    "telegram",
                    "--bot-id",
                    "test",
                    "--token",
                    "abc123",
                ],
            )

        _assert_ok(result, label="bot add")
        assert "test" in result.output or "stored" in result.output.lower()

        token = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert token == "abc123"

    def test_bot_add_overwrite_confirmed_stores_new_token(self, tmp_path: Path) -> None:
        """When credential exists and user confirms, new token is stored."""
        asyncio.run(_setup_store(tmp_path, [("telegram", "test", "old-token", None)]))

        with _patch_vault(tmp_path):
            with patch("lyra.cli_bot.typer.confirm", return_value=True) as mock_confirm:
                result = runner.invoke(
                    bot_app,
                    [
                        "add",
                        "--platform",
                        "telegram",
                        "--bot-id",
                        "test",
                        "--token",
                        "new-token",
                    ],
                )

        mock_confirm.assert_called_once()
        _assert_ok(result, label="bot add overwrite")
        stored = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert stored == "new-token"

    def test_bot_add_overwrite_aborted_keeps_old_token(self, tmp_path: Path) -> None:
        """When credential exists and user aborts confirm, old token is kept."""
        asyncio.run(_setup_store(tmp_path, [("telegram", "test", "old-token", None)]))

        import typer as _typer

        with _patch_vault(tmp_path):
            with patch("lyra.cli_bot.typer.confirm", side_effect=_typer.Abort()):
                result = runner.invoke(
                    bot_app,
                    [
                        "add",
                        "--platform",
                        "telegram",
                        "--bot-id",
                        "test",
                        "--token",
                        "new-token",
                    ],
                )

        assert result.exit_code != 0
        stored = asyncio.run(_fresh_read(tmp_path, "telegram", "test"))
        assert stored == "old-token"

    def test_bot_add_invalid_platform_exits_nonzero(self, tmp_path: Path) -> None:
        """Providing an unsupported platform prints an error and exits non-zero."""
        # No store needed — validation fires before _make_store is called
        with _patch_vault(tmp_path):
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform",
                    "slack",
                    "--bot-id",
                    "test",
                    "--token",
                    "abc",
                ],
            )

        assert result.exit_code != 0
        assert "slack" in result.output or "Unknown platform" in result.output

    def test_bot_add_with_webhook_secret(self, tmp_path: Path) -> None:
        """Webhook secret is stored alongside the token."""
        asyncio.run(_setup_store(tmp_path))

        with _patch_vault(tmp_path):
            result = runner.invoke(
                bot_app,
                [
                    "add",
                    "--platform",
                    "telegram",
                    "--bot-id",
                    "main",
                    "--token",
                    "tok123",
                    "--webhook-secret",
                    "mysecret",
                ],
            )

        _assert_ok(result, label="bot add webhook-secret")
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
        asyncio.run(
            _setup_store(tmp_path, [("telegram", "main", "super-secret-token", None)])
        )

        with _patch_vault(tmp_path):
            result = runner.invoke(bot_app, ["list"])

        _assert_ok(result, label="bot list masked")
        assert "***..." in result.output
        assert "oken" in result.output  # last 4 chars of "super-secret-token"

    def test_bot_list_empty_state(self, tmp_path: Path) -> None:
        """Empty credential store shows the 'No credentials stored' message."""
        asyncio.run(_setup_store(tmp_path))

        with _patch_vault(tmp_path):
            result = runner.invoke(bot_app, ["list"])

        _assert_ok(result, label="bot list empty")
        assert "No credentials stored" in result.output

    def test_bot_list_multiple_bots(self, tmp_path: Path) -> None:
        """Multiple bots are all listed, each with a masked token."""
        asyncio.run(
            _setup_store(
                tmp_path,
                [
                    ("telegram", "bot-a", "token-aaa", None),
                    ("discord", "bot-b", "token-bbb", None),
                ],
            )
        )

        with _patch_vault(tmp_path):
            result = runner.invoke(bot_app, ["list"])

        assert result.exit_code == 0
        assert "telegram" in result.output
        assert "discord" in result.output
        assert "bot-a" in result.output
        assert "bot-b" in result.output

    def test_bot_list_shows_header(self, tmp_path: Path) -> None:
        """The list output includes PLATFORM, BOT ID, TOKEN column headers."""
        asyncio.run(
            _setup_store(tmp_path, [("telegram", "main", "some-token-xyz", None)])
        )

        with _patch_vault(tmp_path):
            result = runner.invoke(bot_app, ["list"])

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
        asyncio.run(_setup_store(tmp_path, [("telegram", "main", "some-token", None)]))

        with _patch_vault(tmp_path):
            with patch("lyra.cli_bot.typer.confirm", return_value=True):
                result = runner.invoke(
                    bot_app,
                    ["remove", "--platform", "telegram", "--bot-id", "main"],
                )

        _assert_ok(result, label="bot remove existing")
        assert "Removed" in result.output or "removed" in result.output.lower()

        token = asyncio.run(_fresh_read(tmp_path, "telegram", "main"))
        assert token is None

    def test_bot_remove_nonexistent(self, tmp_path: Path) -> None:
        """Removing a non-existent bot prints 'Not found'."""
        asyncio.run(_setup_store(tmp_path))

        with _patch_vault(tmp_path):
            with patch("lyra.cli_bot.typer.confirm", return_value=True):
                result = runner.invoke(
                    bot_app,
                    ["remove", "--platform", "telegram", "--bot-id", "ghost"],
                )

        _assert_ok(result, label="bot remove nonexistent")
        assert "Not found" in result.output or "not found" in result.output.lower()

    def test_bot_remove_aborted_keeps_credential(self, tmp_path: Path) -> None:
        """When user aborts the confirm prompt, credential is not deleted."""
        asyncio.run(_setup_store(tmp_path, [("telegram", "main", "keep-me", None)]))

        import typer as _typer

        # typer.confirm is called at sync level in bot_remove (before asyncio.run)
        with patch("lyra.cli_bot.typer.confirm", side_effect=_typer.Abort()):
            result = runner.invoke(
                bot_app,
                ["remove", "--platform", "telegram", "--bot-id", "main"],
            )

        assert result.exit_code != 0
        token = asyncio.run(_fresh_read(tmp_path, "telegram", "main"))
        assert token == "keep-me"

    def test_bot_remove_confirm_called_with_platform_and_bot_id(
        self, tmp_path: Path
    ) -> None:
        """The confirm prompt message includes the platform and bot_id."""
        asyncio.run(_setup_store(tmp_path, [("discord", "prod-bot", "dc-token", None)]))

        prompt_messages: list[str] = []

        def _recording_confirm(msg: str, **kwargs: object) -> bool:
            prompt_messages.append(msg)
            return True

        with _patch_vault(tmp_path):
            with patch("lyra.cli_bot.typer.confirm", side_effect=_recording_confirm):
                runner.invoke(
                    bot_app,
                    [
                        "remove",
                        "--platform",
                        "discord",
                        "--bot-id",
                        "prod-bot",
                    ],
                )

        assert len(prompt_messages) == 1
        assert "discord" in prompt_messages[0]
        assert "prod-bot" in prompt_messages[0]
