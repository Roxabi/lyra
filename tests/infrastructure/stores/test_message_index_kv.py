"""Tests for MessageIndexKvStore (#1059)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import BadRequestError, KeyNotFoundError

from factory.infrastructure.stores.message_index_kv import (
    KV_BUCKET,
    MessageIndexKvStore,
    _sanitize_key_part,
    ensure_kv,
)


class TestMessageIndexKvStore:
    """Unit tests for MessageIndexKvStore."""

    async def test_upsert_and_resolve(self):
        kv = AsyncMock()
        entry = MagicMock()
        entry.value = "sess-abc".encode()
        kv.get.return_value = entry
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool:tg:main", "msg-123", "sess-abc", "user")
        result = await store.resolve("pool:tg:main", "msg-123")
        assert result == "sess-abc"
        kv.put.assert_awaited_once_with("pool:tg:main:msg-123", "sess-abc".encode())

    async def test_upsert_skips_none_msg_id(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool:tg:main", None, "sess-abc", "assistant")
        kv.put.assert_not_awaited()

    async def test_resolve_not_found(self):
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        result = await store.resolve("pool:tg:main", "nonexistent")
        assert result is None

    async def test_resolve_scoped_by_pool_id(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool:tg:chat1", "msg-1", "sess-a", "user")
        await store.upsert("pool:tg:chat2", "msg-1", "sess-b", "user")

        calls = [c.args[0] for c in kv.put.await_args_list]
        assert calls == ["pool:tg:chat1:msg-1", "pool:tg:chat2:msg-1"]

        async def _get(key):
            if key == "pool:tg:chat1:msg-1":
                entry = MagicMock()
                entry.value = "sess-a".encode()
                return entry
            if key == "pool:tg:chat2:msg-1":
                entry = MagicMock()
                entry.value = "sess-b".encode()
                return entry
            raise KeyNotFoundError

        kv.get.side_effect = _get
        assert await store.resolve("pool:tg:chat1", "msg-1") == "sess-a"
        assert await store.resolve("pool:tg:chat2", "msg-1") == "sess-b"

    async def test_cleanup_older_than_returns_zero(self):
        js = AsyncMock()
        store = MessageIndexKvStore(js)
        assert await store.cleanup_older_than(30) == 0

    async def test_both_roles_indexed(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool:tg:main", "msg-user", "sess-1", "user")
        await store.upsert("pool:tg:main", "msg-bot", "sess-1", "assistant")
        calls = [c.args[0] for c in kv.put.await_args_list]
        assert "pool:tg:main:msg-user" in calls
        assert "pool:tg:main:msg-bot" in calls

    async def test_connect_and_close(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        assert store._kv is None
        await store.connect()
        assert store._kv is kv
        await store.close()
        assert store._kv is None

    async def test_require_kv_guard(self):
        js = AsyncMock()
        store = MessageIndexKvStore(js)
        with pytest.raises(RuntimeError, match="call connect"):
            await store.resolve("pool:tg:main", "msg-1")
        with pytest.raises(RuntimeError, match="call connect"):
            await store.upsert("pool:tg:main", "msg-1", "sess-a", "user")

    async def test_ensure_kv_creates_or_binds(self):
        js = AsyncMock()
        kv_created = AsyncMock()
        kv_bound = AsyncMock()
        js.create_key_value.return_value = kv_created
        js.key_value.return_value = kv_bound

        # Success path: bucket created
        result = await ensure_kv(js, retention_days=7)
        assert result is kv_created

        # Fallback path: bucket exists, bind instead
        js.create_key_value.side_effect = BadRequestError
        result = await ensure_kv(js, retention_days=7)
        assert result is kv_bound
        js.key_value.assert_awaited_once_with(KV_BUCKET)

    async def test_upsert_sanitizes_key(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool.tg.main", "msg.123", "sess-abc", "user")
        await store.upsert("pool:tg:*main>", "msg*>123", "sess-xyz", "assistant")
        calls = [c.args[0] for c in kv.put.await_args_list]
        assert calls == [
            "pool_tg_main:msg_123",
            "pool:tg:_main_:msg__123",
        ]

    async def test_upsert_with_empty_platform_msg_id(self):
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = MessageIndexKvStore(js)
        await store.connect()

        await store.upsert("pool:tg:main", None, "sess-abc", "user")
        kv.put.assert_not_awaited()


class TestSanitizeHelpers:
    """Tests for key sanitization and validation helpers."""

    def test_sanitize_key_part_replaces_metacharacters(self):
        assert _sanitize_key_part("a.b*c>d") == "a_b_c_d"

    def test_sanitize_key_part_returns_safe_value(self):
        assert _sanitize_key_part("safe_key") == "safe_key"
