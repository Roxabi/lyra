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

from unittest.mock import AsyncMock, patch

import pytest

# Patch targets: must match where start_audio_consumer is imported in the
# wiring modules.  If these paths move, mypy/pyright will NOT catch it — but
# the patched tests will start failing immediately, making the drift visible.
_PATCH_TARGETS = (
    "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
    "lyra.bootstrap.wiring.standalone_discord.start_audio_consumer",
)


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
    with (
        patch(_PATCH_TARGETS[0], noop),
        patch(_PATCH_TARGETS[1], noop),
    ):
        yield
