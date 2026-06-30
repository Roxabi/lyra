"""Unit tests for factory-events / factory-metrics stream provisioning."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import nats.errors
import pytest
from nats.js.errors import BadRequestError

from factory.infrastructure.events.stream_setup import (
    STREAM_EVENTS,
    STREAM_METRICS,
    _events_config,
    _metrics_config,
    ensure_events_stream,
    ensure_metrics_stream,
    ensure_observability_streams,
)


@pytest.mark.anyio
async def test_ensure_events_stream_add_success() -> None:
    js = MagicMock()
    js.add_stream = AsyncMock(return_value=MagicMock())
    js.update_stream = AsyncMock()

    await ensure_events_stream(js)

    js.add_stream.assert_awaited_once_with(_events_config())
    js.update_stream.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_metrics_stream_update_on_existing() -> None:
    js = MagicMock()
    js.add_stream = AsyncMock(side_effect=BadRequestError())
    js.update_stream = AsyncMock(return_value=MagicMock())
    expected_cfg = _metrics_config()

    await ensure_metrics_stream(js)

    js.update_stream.assert_awaited_once_with(expected_cfg)


@pytest.mark.anyio
async def test_ensure_observability_streams_idempotent() -> None:
    js = MagicMock()
    js.add_stream = AsyncMock(return_value=MagicMock())
    js.update_stream = AsyncMock()

    await ensure_observability_streams(js)
    await ensure_observability_streams(js)

    assert js.add_stream.await_count == 4


@pytest.mark.anyio
async def test_ensure_events_stream_add_error_propagates() -> None:
    js = MagicMock()
    js.add_stream = AsyncMock(side_effect=nats.errors.Error())
    js.update_stream = AsyncMock()

    with pytest.raises(nats.errors.Error):
        await ensure_events_stream(js)

    js.update_stream.assert_not_awaited()


def test_stream_names() -> None:
    assert STREAM_EVENTS == "factory-events"
    assert STREAM_METRICS == "factory-metrics"


def test_events_subjects_cover_ingress_tenant_pattern() -> None:
    cfg = _events_config()
    assert cfg.subjects == ["factory.event.>"]