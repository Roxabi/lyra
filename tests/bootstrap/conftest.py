"""Bootstrap test conftest — shared fixtures for tests/bootstrap/.

Provides an autouse no-op patch for start_audio_consumer so that pre-existing
bootstrap tests that don't need audio consumer wiring are not broken by T8.

Also provides autouse stubs for the KvLastSessionStore and _create_dc_stores
wiring added in #1721, so pre-existing bootstrap tests that don't exercise
KV last-session are not broken by the new wiring.

Tests that exercise audio consumer behaviour directly must opt out of this
no-op by marking themselves with ``@pytest.mark.audio_consumer_live``.  This
is more robust than a filename-string check: the marker survives module renames
and is visible on the test node without inspecting the nodeid.

The patch targets are the *import sites* in the wiring modules (innermost
`with patch(...)` wins if a test also patches them).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch target: must match where start_audio_consumer is imported.
# After #1663 refactor, the call site moved into _standalone_wiring_common —
# a single patch covers both Telegram and Discord paths.
_PATCH_TARGETS = (
    "factory.bootstrap.wiring._standalone_wiring_common.start_audio_consumer",
)

# KvLastSessionStore patch targets — added #1721.
_KV_STORE_TG = "factory.bootstrap.wiring.standalone_telegram.KvLastSessionStore"
_KV_STORE_DC = "factory.bootstrap.wiring.standalone_discord.KvLastSessionStore"
# _create_dc_stores patch target — prevents real ThreadStore(discord.db) opens.
_DC_STORES = "factory.bootstrap.wiring.standalone_discord._create_dc_stores"


def _make_kv_store_stub() -> MagicMock:
    """Return a KvLastSessionStore class stub (connect/close = AsyncMock)."""
    stub = MagicMock()
    stub.return_value.connect = AsyncMock()
    stub.return_value.close = AsyncMock()
    return stub


def _make_dc_thread_store_stub() -> AsyncMock:
    """Return a Discord ThreadStore stub for _create_dc_stores."""
    ts = AsyncMock()
    ts.connect = AsyncMock()
    ts.close = AsyncMock()
    return ts


@pytest.fixture(autouse=True)
def _noop_audio_consumer(request: pytest.FixtureRequest) -> object:
    """Patch start_audio_consumer to a no-op for all bootstrap tests.

    Tests marked with ``@pytest.mark.audio_consumer_live`` receive the real
    (or their own) implementation and are NOT neutralized.
    """
    if request.node.get_closest_marker("audio_consumer_live") is not None:
        yield
        return

    noop = AsyncMock(return_value=AsyncMock())
    with patch(_PATCH_TARGETS[0], noop):
        yield


@pytest.fixture(autouse=True)
def _noop_kv_last_session() -> object:
    """Stub KvLastSessionStore for all bootstrap tests (#1721).

    Prevents real NATS calls in tests that boot standalone adapters but do not
    test KV last-session behaviour.  Tests in test_adapter_boot_no_turns_db.py
    that assert on the KV store type override this with their own inner patch.
    """
    _dc_ts = _make_dc_thread_store_stub()
    with (
        patch(_KV_STORE_TG, _make_kv_store_stub()),
        patch(_KV_STORE_DC, _make_kv_store_stub()),
        patch(_DC_STORES, AsyncMock(return_value=(_dc_ts,))),
    ):
        yield
