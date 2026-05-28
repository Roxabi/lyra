"""Bootstrap test conftest — shared fixtures for tests/bootstrap/.

Provides an autouse no-op patch for start_audio_consumer so that pre-existing
bootstrap tests that don't need audio consumer wiring are not broken by T8.

Tests in test_bootstrap_audio_consumer.py override this by patching
start_audio_consumer at the same path inside their own `with patch(...)` blocks
(innermost patch wins), or by patching the underlying ensure_*/JetStreamAudioConsumer
symbols via a side_effect that calls through to the real function.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _noop_audio_consumer(request):
    """Patch start_audio_consumer to a no-op for all bootstrap tests.

    Tests in test_bootstrap_audio_consumer.py override this by applying their
    own patch inside the test body — the innermost `with patch(...)` wins.
    """
    # Skip the patch for tests that explicitly manage audio consumer mocking.
    # They identify themselves via the marker or by their module name.
    if "test_bootstrap_audio_consumer" in request.node.nodeid:
        yield
        return

    noop = AsyncMock(return_value=AsyncMock())
    with (
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            noop,
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.start_audio_consumer",
            noop,
        ),
    ):
        yield
