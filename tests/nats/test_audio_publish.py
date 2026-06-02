"""Tests for audio publish helpers with retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import nats.errors
import pytest

from factory.nats.audio_publish import (
    _AUDIO_PUBLISH_MAX_ATTEMPTS,
    publish_audio_with_retry,
)


@pytest.mark.asyncio
async def test_publish_audio_with_retry_first_attempt_succeeds_no_sleep() -> None:
    """When the first publish succeeds, asyncio.sleep must not be called."""
    js = AsyncMock()
    js.publish.side_effect = None

    with patch("factory.nats.audio_publish.asyncio.sleep") as mock_sleep:
        await publish_audio_with_retry(js, "subject", b"payload", "stream-id")

    js.publish.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_audio_with_retry_all_attempts_fail_sleep_count() -> None:
    """All attempts fail: sleep is called MAX_ATTEMPTS - 1 times (guard on last)."""
    js = AsyncMock()
    js.publish.side_effect = nats.errors.Error("publish failed")

    with patch("factory.nats.audio_publish.asyncio.sleep") as mock_sleep:
        with pytest.raises(nats.errors.Error):
            await publish_audio_with_retry(js, "subject", b"payload", "stream-id")

    assert js.publish.await_count == _AUDIO_PUBLISH_MAX_ATTEMPTS
    assert mock_sleep.await_count == _AUDIO_PUBLISH_MAX_ATTEMPTS - 1
