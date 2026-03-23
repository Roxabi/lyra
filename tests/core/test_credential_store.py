"""RED-phase tests for CredentialStore + LyraKeyring (issue #262, S1).

All tests in this file are expected to FAIL with ImportError or
ModuleNotFoundError until lyra.core.credential_store is implemented.
"""

from __future__ import annotations

import base64
import sqlite3
import stat
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from lyra.core.stores.credential_store import BotSecretRow, CredentialStore, LyraKeyring
from lyra.errors import KeyringError

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


async def make_store(tmp_path: Path) -> CredentialStore:
    """Create and connect a real CredentialStore backed by a tmp file DB.

    Prefer the ``credential_store`` pytest fixture for new tests — it provides
    automatic teardown via ``yield`` + ``await store.close()``.
    """
    key_path = tmp_path / "lyra.key"
    keyring = LyraKeyring.load_or_create(key_path)
    store = CredentialStore(db_path=str(tmp_path / "credentials.db"), keyring=keyring)
    await store.connect()
    return store


@pytest.fixture
async def credential_store(tmp_path: Path) -> AsyncGenerator[CredentialStore, None]:
    """Fixture-based CredentialStore with automatic teardown."""
    store = await make_store(tmp_path)
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# TestCredentialStoreRoundtrip
# ---------------------------------------------------------------------------


class TestCredentialStoreRoundtrip:
    """CredentialStore.set() / get() — encryption, retrieval, overwrites."""

    async def test_set_and_get_roundtrip(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            platform = "telegram"
            bot_id = "main"
            token = "secret-bot-token-abc123"

            # Act
            await store.set(platform, bot_id, token)
            result = await store.get(platform, bot_id)

            # Assert
            assert result == token, (
                f"get() must return the original plaintext token, got {result!r}"
            )
        finally:
            await store.close()

    async def test_token_encrypted_in_db(self, tmp_path: Path) -> None:
        # Arrange
        db_path = str(tmp_path / "credentials.db")
        key_path = tmp_path / "lyra.key"
        keyring = LyraKeyring.load_or_create(key_path)
        store = CredentialStore(db_path=db_path, keyring=keyring)
        await store.connect()
        try:
            token = "plaintext-secret-token"

            # Act
            await store.set("telegram", "main", token)
            await store.close()

            # Assert — raw sqlite3 read must not expose plaintext
            con = sqlite3.connect(db_path)
            rows = con.execute("SELECT token FROM bot_secrets").fetchall()
            con.close()
            for (stored_value,) in rows:
                assert token not in stored_value, (
                    "plaintext token must not appear verbatim in the DB column"
                )
        finally:
            # store already closed above; avoid double-close errors
            pass

    async def test_set_overwrites(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            platform = "telegram"
            bot_id = "main"

            # Act
            await store.set(platform, bot_id, "first-token")
            await store.set(platform, bot_id, "second-token")
            result = await store.get(platform, bot_id)
            all_rows = await store.list_all()

            # Assert
            assert result == "second-token", "second set() must overwrite the first"
            assert len(all_rows) == 1, (
                "list_all() must return exactly 1 row after overwrite, "
                f"got {len(all_rows)}"
            )
        finally:
            await store.close()

    async def test_get_returns_none_for_missing(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            # Act
            result = await store.get("discord", "absent-bot")

            # Assert
            assert result is None, (
                "get() must return None for a (platform, bot_id) that was never stored"
            )
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestCredentialStoreDelete
# ---------------------------------------------------------------------------


class TestCredentialStoreDelete:
    """CredentialStore.delete() — return value and post-delete state."""

    async def test_delete_existing(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            await store.set("telegram", "main", "some-token")

            # Act
            deleted = await store.delete("telegram", "main")
            result = await store.get("telegram", "main")

            # Assert
            assert deleted is True, "delete() must return True when the row existed"
            assert result is None, "get() must return None after delete()"
        finally:
            await store.close()

    async def test_delete_nonexistent(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            # Act
            deleted = await store.delete("discord", "ghost-bot")

            # Assert
            assert deleted is False, (
                "delete() must return False when no row matched (platform, bot_id)"
            )
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestCredentialStoreListExists
# ---------------------------------------------------------------------------


class TestCredentialStoreListExists:
    """CredentialStore.list_all() + exists() — enumeration and presence check."""

    async def test_list_all_empty(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            # Act
            rows = await store.list_all()

            # Assert
            assert rows == [], (
                "list_all() must return an empty list when no secrets are stored"
            )
        finally:
            await store.close()

    async def test_list_all_multiple(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            await store.set("telegram", "main", "tg-token")
            await store.set("discord", "secondary", "dc-token")

            # Act
            rows = await store.list_all()

            # Assert
            assert len(rows) == 2, (
                "list_all() must return 2 rows after storing 2 entries, "
                f"got {len(rows)}"
            )
            assert all(isinstance(r, BotSecretRow) for r in rows), (
                "list_all() must return BotSecretRow instances"
            )
            platforms = {r.platform for r in rows}
            bot_ids = {r.bot_id for r in rows}
            assert platforms == {"telegram", "discord"}
            assert bot_ids == {"main", "secondary"}
        finally:
            await store.close()

    async def test_exists_true_false(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            await store.set("telegram", "main", "some-token")

            # Act + Assert
            assert await store.exists("telegram", "main") is True, (
                "exists() must return True for a stored (platform, bot_id)"
            )
            assert await store.exists("discord", "absent") is False, (
                "exists() must return False for an absent (platform, bot_id)"
            )
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestLyraKeyring
# ---------------------------------------------------------------------------


class TestLyraKeyring:
    """LyraKeyring.load_or_create() — key creation, persistence, permissions, errors."""

    async def test_keyring_atomic_creation(self, tmp_path: Path) -> None:
        # Arrange
        key_path = tmp_path / "lyra.key"

        # Act
        LyraKeyring.load_or_create(key_path)

        # Assert — file must exist with 0o600 permissions
        assert key_path.exists(), "load_or_create() must create the key file"
        file_mode = stat.S_IMODE(key_path.stat().st_mode)
        assert file_mode == 0o600, (
            f"key file must have 0o600 permissions, got {oct(file_mode)}"
        )

        # Assert — valid Fernet key: urlsafe base64, 32 raw bytes → 44 chars
        raw_content = key_path.read_bytes().strip()
        assert len(raw_content) == 44, (
            f"Fernet key must be 44 bytes of urlsafe base64, got {len(raw_content)}"
        )
        decoded = base64.urlsafe_b64decode(raw_content)
        assert len(decoded) == 32, (
            f"decoded Fernet key must be 32 bytes, got {len(decoded)}"
        )

    async def test_keyring_load_existing(self, tmp_path: Path) -> None:
        # Arrange
        key_path = tmp_path / "lyra.key"
        keyring1 = LyraKeyring.load_or_create(key_path)

        # Act — second call must load the same key, not regenerate it
        keyring2 = LyraKeyring.load_or_create(key_path)

        # Assert
        assert keyring1.key == keyring2.key, (
            "load_or_create() must return the same key bytes on subsequent calls"
        )

    async def test_keyring_unreadable_raises_keyring_error(
        self, tmp_path: Path
    ) -> None:
        # Arrange — create the key file first, then make it unreadable
        key_path = tmp_path / "lyra.key"
        LyraKeyring.load_or_create(key_path)
        key_path.chmod(0o000)

        try:
            # Act + Assert
            with pytest.raises(KeyringError):
                LyraKeyring.load_or_create(key_path)
        finally:
            # Restore permissions so tmp_path cleanup can delete the file
            key_path.chmod(0o600)
