"""Tests for stream error publish helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import nats.errors
import pytest

from factory.nats.stream_error import publish_stream_error, publish_stream_errors


@pytest.mark.asyncio
async def test_publish_stream_error_swallows_nats_error() -> None:
    """publish_stream_error must not raise when nc.publish raises nats.errors.Error."""
    nc = AsyncMock()
    nc.publish.side_effect = nats.errors.Error("publish failed")

    await publish_stream_error(nc, "subject", "stream-id")

    nc.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_stream_errors_swallows_nats_error() -> None:
    """publish_stream_errors must not raise when nc.publish raises nats.errors.Error."""
    nc = AsyncMock()
    nc.publish.side_effect = nats.errors.Error("publish failed")

    await publish_stream_errors(nc, "subject", {"stream-1", "stream-2"})

    assert nc.publish.await_count == 2


@pytest.mark.asyncio
async def test_publish_stream_errors_iterates_all_despite_failures() -> None:
    """All stream_ids must be attempted even if individual publishes fail."""
    nc = AsyncMock()
    nc.publish.side_effect = nats.errors.Error("publish failed")

    stream_ids = {"stream-a", "stream-b", "stream-c"}
    await publish_stream_errors(nc, "subject", stream_ids)

    assert nc.publish.await_count == 3
    # Verify all stream_ids appear in the published payloads
    published_stream_ids = set()
    for call in nc.publish.call_args_list:
        payload = json.loads(call.args[1].decode("utf-8"))
        published_stream_ids.add(payload["stream_id"])

    assert published_stream_ids == stream_ids
