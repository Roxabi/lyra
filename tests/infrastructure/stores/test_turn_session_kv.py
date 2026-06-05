"""Unit tests for KvLastSessionStore + TurnStoreLastSession (#1721)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import KeyNotFoundError, NoKeysError

from factory.infrastructure.stores.turn_session_kv import (
    KV_BUCKET,
    KvLastSessionStore,
    ensure_kv,
)

# ---------------------------------------------------------------------------
# T20 — KvLastSessionStore
# ---------------------------------------------------------------------------


class TestKvBucketName:
    """Byte-match guard: KV_BUCKET must equal the ACL subject prefix (D8)."""

    def test_kv_bucket_name_matches_acl_prefix(self) -> None:
        """KV_BUCKET literal must byte-match 'factory-turns-meta' (D8).

        Negative: if the literal is renamed, the ACL subjects $KV.factory-turns-meta.>
        silently deny kv.put / kv.get at runtime; the degrade-to-new-session path hides
        the breakage.  This test turns that silent failure into a red gate.
        """
        assert KV_BUCKET == "factory-turns-meta"


class TestKvLastSessionStoreRoundTrip:
    """KvLastSessionStore: put→get round-trip and miss→None."""

    async def test_put_get_round_trip(self) -> None:
        """set_last_session + get_last_session returns the stored value."""
        # Arrange
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvLastSessionStore(js, retention_days=90)
        await store.connect()

        # Act — set first; seed kv.get AFTER set so a missing put would surface
        await store.set_last_session("pool-tg-main", "sess-abc")

        # Seed the read return value only now — ensures put was called before get
        entry = MagicMock()
        entry.value = b"sess-abc"
        kv.get.return_value = entry

        result = await store.get_last_session("pool-tg-main")

        # Assert — Negative: removing the put call causes kv.put assertion to fail
        kv.put.assert_awaited_once_with("last_session.pool-tg-main", b"sess-abc")
        kv.get.assert_awaited_once_with("last_session.pool-tg-main")
        assert result == "sess-abc"

    async def test_get_miss_key_not_found_returns_none(self) -> None:
        """get_last_session returns None when the key is absent (KeyNotFoundError)."""
        # Arrange
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvLastSessionStore(js)
        await store.connect()

        # Act
        result = await store.get_last_session("pool-tg-main")

        # Assert — Negative: removing the KeyNotFoundError catch causes this to raise
        assert result is None

    async def test_get_miss_no_keys_returns_none(self) -> None:
        """get_last_session returns None when bucket is empty (NoKeysError)."""
        # Arrange
        kv = AsyncMock()
        kv.get.side_effect = NoKeysError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvLastSessionStore(js)
        await store.connect()

        # Act
        result = await store.get_last_session("pool-tg-main")

        # Assert — Negative: removing the NoKeysError catch causes this to raise
        assert result is None

    async def test_get_returns_none_when_entry_value_is_empty_bytes(self) -> None:
        """get_last_session returns None when entry.value is empty/None."""
        # Arrange
        kv = AsyncMock()
        entry = MagicMock()
        entry.value = b""
        kv.get.return_value = entry
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvLastSessionStore(js)
        await store.connect()

        # Act
        result = await store.get_last_session("pool-tg-main")

        # Assert — falsy bytes → None (empty session_id is not valid)
        assert result is None


class TestKvLastSessionStoreConnectMissingBucket:
    """connect() against a missing bucket must NOT raise; subsequent ops degrade."""

    async def test_connect_missing_bucket_sets_kv_none_no_raise(self) -> None:
        """connect() on a missing bucket degrades to _kv=None; does not raise.

        Negative: if connect() raises, adapter boot crashes before hub provisions
        the bucket — violating the cold-boot degrade-to-new-session contract.
        """
        from nats.js.errors import BucketNotFoundError

        # Arrange — simulate a missing bucket (cold boot). #7 narrowed connect()'s
        # catch to BucketNotFoundError, so the mock must raise that exact type —
        # a generic nats.errors.Error now correctly propagates instead of degrading.
        js = AsyncMock()
        js.key_value.side_effect = BucketNotFoundError()
        store = KvLastSessionStore(js)

        # Act — must not raise
        await store.connect()

        # Assert — _kv is None after failed bind
        assert store._kv is None

    async def test_get_last_session_with_kv_none_returns_none(self) -> None:
        """get_last_session returns None when _kv is None (degrade path).

        Negative: removing the `if self._kv is None: return None` guard causes
        AttributeError on None._kv.get(), breaking the degrade contract.
        """
        # Arrange — no connect() called, _kv remains None
        js = AsyncMock()
        store = KvLastSessionStore(js)

        # Act
        result = await store.get_last_session("pool-tg-main")

        # Assert — degrade: no KV handle → new-session path
        assert result is None

    async def test_set_last_session_with_kv_none_is_silent_noop(self) -> None:
        """set_last_session is a no-op when _kv is None (degrade path)."""
        # Arrange
        js = AsyncMock()
        store = KvLastSessionStore(js)

        # Act — must not raise
        await store.set_last_session("pool-tg-main", "sess-xyz")

        # Assert — no KV call happened (no crash either)
        js.key_value.assert_not_awaited()

    async def test_connect_non_bucket_error_propagates(self) -> None:
        """connect() propagates non-BucketNotFoundError NATS errors (#7).

        #7 narrowed connect()'s catch to BucketNotFoundError only — errors such
        as nats.errors.TimeoutError (NATS unreachable) must NOT silently degrade
        _kv to None; they must propagate so the caller can handle real failures.

        Negative: if connect() reverted to catching all nats.errors.Error, this
        test would stop raising and the silent-degrade bug would reappear.
        """
        import nats.errors

        # Arrange — a timeout is a real NATS failure, NOT a cold-boot bucket miss
        js = AsyncMock()
        js.key_value.side_effect = nats.errors.TimeoutError()
        store = KvLastSessionStore(js)

        # Act + Assert — must raise, must NOT silently set _kv=None
        with pytest.raises(nats.errors.TimeoutError):
            await store.connect()

        # _kv must remain None (uninitialised), not silently degraded
        assert store._kv is None

    async def test_connect_sets_kv_on_success(self) -> None:
        """connect() sets _kv to the bound KeyValue handle on success."""
        # Arrange
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvLastSessionStore(js)
        assert store._kv is None

        # Act
        await store.connect()

        # Assert
        assert store._kv is kv


class TestEnsureKv:
    """ensure_kv: create-or-bind idempotent bucket provision."""

    async def test_ensure_kv_creates_bucket_on_first_call(self) -> None:
        """ensure_kv creates the bucket when it does not exist."""

        js = AsyncMock()
        kv_created = AsyncMock()
        js.create_key_value.return_value = kv_created

        result = await ensure_kv(js, retention_days=90)

        assert result is kv_created
        js.create_key_value.assert_awaited_once()
        config_arg = js.create_key_value.await_args[0][0]
        assert config_arg.bucket == KV_BUCKET

    async def test_ensure_kv_binds_on_already_exists(self) -> None:
        """ensure_kv falls back to key_value() when bucket already exists."""
        from nats.js.errors import BadRequestError

        js = AsyncMock()
        kv_bound = AsyncMock()
        js.create_key_value.side_effect = BadRequestError
        js.key_value.return_value = kv_bound

        result = await ensure_kv(js, retention_days=90)

        assert result is kv_bound
        js.key_value.assert_awaited_once_with(KV_BUCKET)


# ---------------------------------------------------------------------------
# T20 — TurnStoreLastSession
# ---------------------------------------------------------------------------


class TestTurnStoreLastSession:
    """TurnStoreLastSession: get delegates to TurnStore; set is a no-op."""

    async def test_get_last_session_delegates_to_turn_store(self) -> None:
        """get_last_session returns the value from the wrapped TurnStore."""
        from factory.bootstrap.wiring.last_session_wiring import TurnStoreLastSession

        # Arrange
        ts = MagicMock()
        ts.get_last_session = AsyncMock(return_value="sess-file-backed")
        wrapper = TurnStoreLastSession(ts)

        # Act
        result = await wrapper.get_last_session("pool-hub-cli")

        # Assert — delegates; Negative: if get delegates to KV instead, this fails
        ts.get_last_session.assert_awaited_once_with("pool-hub-cli")
        assert result == "sess-file-backed"

    async def test_set_last_session_is_noop_turn_store_never_called(self) -> None:
        """set_last_session is a no-op; TurnStore.set is never invoked.

        Negative: if set_last_session called ts.set_last_session or ts.start_session,
        it would violate ADR-075 (turn_writer is the sole turns.db writer).
        """
        from factory.bootstrap.wiring.last_session_wiring import TurnStoreLastSession

        # Arrange
        ts = MagicMock()
        ts.set_last_session = AsyncMock()
        ts.start_session = AsyncMock()
        wrapper = TurnStoreLastSession(ts)

        # Act — must not raise, must not call any write method
        await wrapper.set_last_session("pool-hub-cli", "sess-new")

        # Assert — no write method called on wrapped store
        ts.set_last_session.assert_not_awaited()
        ts.start_session.assert_not_awaited()

    async def test_get_returns_none_when_turn_store_returns_none(self) -> None:
        """get_last_session propagates None from TurnStore (no prior session)."""
        from factory.bootstrap.wiring.last_session_wiring import TurnStoreLastSession

        ts = MagicMock()
        ts.get_last_session = AsyncMock(return_value=None)
        wrapper = TurnStoreLastSession(ts)

        result = await wrapper.get_last_session("pool-hub-cli")

        assert result is None

    def test_turn_store_last_session_satisfies_last_session_store_protocol(
        self,
    ) -> None:
        """TurnStoreLastSession satisfies LastSessionStore Protocol."""
        from factory.bootstrap.wiring.last_session_wiring import TurnStoreLastSession
        from factory.core.ports.last_session_store import LastSessionStore

        ts = MagicMock()
        wrapper = TurnStoreLastSession(ts)

        # LastSessionStore is runtime_checkable
        assert isinstance(wrapper, LastSessionStore)

    async def test_kv_last_session_store_satisfies_last_session_store_protocol(
        self,
    ) -> None:
        """KvLastSessionStore satisfies LastSessionStore Protocol."""
        from factory.core.ports.last_session_store import LastSessionStore

        js = AsyncMock()
        store = KvLastSessionStore(js)

        assert isinstance(store, LastSessionStore)
