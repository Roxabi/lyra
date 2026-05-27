"""Tests for stream_setup.py public API (ensure_stream + ensure_consumer).

PR #1421 review (Cv 92%) identified that the js.* public-API path had no
coverage. Two test pairs verify the skip-on-exists and create-on-NotFoundError
branches for both ensure_consumer and ensure_stream.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from nats.js.errors import NotFoundError

from lyra.infrastructure.turn_writer.stream_setup import (
    CONSUMER_NAME,
    STREAM_NAME,
    _consumer_config,
    _stream_config,
    ensure_consumer,
    ensure_stream,
)

# ---------------------------------------------------------------------------
# ensure_consumer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_consumer_skips_create_when_exists() -> None:
    """consumer_info returns a value → add_consumer must NOT be called."""
    # Arrange
    js = MagicMock()
    js.consumer_info = AsyncMock(return_value=MagicMock())
    js.add_consumer = AsyncMock()

    # Act
    await ensure_consumer(js)

    # Assert
    js.consumer_info.assert_awaited_once_with(STREAM_NAME, CONSUMER_NAME)
    js.add_consumer.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_consumer_creates_when_not_found() -> None:
    """consumer_info raises NotFoundError → add_consumer called with correct args."""
    # Arrange
    js = MagicMock()
    js.consumer_info = AsyncMock(side_effect=NotFoundError())
    js.add_consumer = AsyncMock(return_value=MagicMock())
    expected_cfg = _consumer_config()

    # Act
    await ensure_consumer(js)

    # Assert
    js.consumer_info.assert_awaited_once_with(STREAM_NAME, CONSUMER_NAME)
    js.add_consumer.assert_awaited_once()
    actual_call = js.add_consumer.await_args
    assert actual_call == call(STREAM_NAME, config=expected_cfg)


# ---------------------------------------------------------------------------
# ensure_stream
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_stream_skips_update_when_add_succeeds() -> None:
    """add_stream succeeds → update_stream must NOT be called."""
    # Arrange
    js = MagicMock()
    js.add_stream = AsyncMock(return_value=MagicMock())
    js.update_stream = AsyncMock()

    # Act
    await ensure_stream(js)

    # Assert
    js.add_stream.assert_awaited_once()
    js.update_stream.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_stream_updates_when_already_exists() -> None:
    """add_stream raises BadRequestError → update_stream called with stream config."""
    from nats.js.errors import BadRequestError

    # Arrange
    js = MagicMock()
    js.add_stream = AsyncMock(side_effect=BadRequestError())
    js.update_stream = AsyncMock(return_value=MagicMock())
    expected_cfg = _stream_config()

    # Act
    await ensure_stream(js)

    # Assert
    js.add_stream.assert_awaited_once()
    js.update_stream.assert_awaited_once()
    actual_call = js.update_stream.await_args
    assert actual_call == call(expected_cfg)
