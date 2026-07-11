"""Bootstrap test conftest — shared fixtures for tests/bootstrap/.

Provides an autouse no-op patch for start_audio_consumer so that pre-existing
bootstrap tests that don't need audio consumer wiring are not broken by T8.

Tests that exercise audio consumer behaviour directly must opt out of this
no-op by marking themselves with ``@pytest.mark.audio_consumer_live``.  This
is more robust than a filename-string check: the marker survives module renames
and is visible on the test node without inspecting the nodeid.

The patch targets are the *import sites* in the wiring modules (innermost
`with patch(...)` wins if a test also patches them).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers.standalone_bot_store import (
    _PATCH_DC,
    _PATCH_TG,
    discord_roster,
    telegram_roster,
)

# Patch target: must match where start_audio_consumer is imported.
# After #1663 refactor, the call site moved into _standalone_wiring_common —
# a single patch covers both Telegram and Discord paths.
_PATCH_TARGETS = (
    "factory.bootstrap.wiring._standalone_wiring_common.start_audio_consumer",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag live-nats bootstrap pipeline tests for the infra CI job."""
    for item in items:
        if Path(item.fspath).name != "test_hub_standalone.py":
            continue
        if "TestStandaloneHubPipeline" not in item.nodeid:
            continue
        item.add_marker(pytest.mark.subprocess_nats)
        item.add_marker(pytest.mark.xdist_group(name="nats_server"))


from tests.factories.nats_server import (  # noqa: F401,E402
    nats_server_url,
    nc,
    requires_nats_server,
)

__all__ = [
    "nats_server_url",
    "nc",
    "requires_nats_server",
]


@pytest.fixture(autouse=True)
def _default_standalone_bot_roster(request: pytest.FixtureRequest) -> object:
    """Default BotStore roster (bot_id='main') for bootstrap adapter tests."""
    if request.node.get_closest_marker("bot_store_live") is not None:
        yield
        return

    with (
        patch(
            _PATCH_TG,
            AsyncMock(return_value=telegram_roster(["main"])),
        ),
        patch(
            _PATCH_DC,
            AsyncMock(
                return_value=discord_roster(
                    ["main"],
                    auto_thread=False,
                    thread_hot_hours=4,
                )
            ),
        ),
    ):
        yield


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
