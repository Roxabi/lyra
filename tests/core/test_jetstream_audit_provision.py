"""Tests for JetStreamAuditSink.provision() (#855) — S3 criteria."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.infrastructure.audit.jetstream_sink import JetStreamAuditSink


def _make_nc(js: object | None = None) -> MagicMock:
    """Return a mock NATS Client whose .jetstream() returns js."""
    nc = MagicMock()
    if js is None:
        nc.jetstream.return_value = _make_js()
    else:
        nc.jetstream.return_value = js
    return nc


def _make_js() -> MagicMock:
    js = MagicMock()
    js.add_stream = AsyncMock()
    js.update_stream = AsyncMock()
    return js


class TestJetStreamAuditSinkProvision:
    async def test_provision_creates_stream(self) -> None:
        """provision() calls js.add_stream with LYRA_AUDIT config."""
        sink = JetStreamAuditSink()
        js = _make_js()
        nc = _make_nc(js)

        await sink.provision(nc)

        js.add_stream.assert_awaited_once()
        cfg = js.add_stream.call_args[0][0]
        assert cfg.name == "LYRA_AUDIT"
        assert "lyra.audit.>" in cfg.subjects

    async def test_provision_stream_config_retention(self) -> None:
        """Provisioned stream has 90-day retention and 1GiB max_bytes."""
        from nats.js.api import RetentionPolicy, StorageType

        sink = JetStreamAuditSink()
        js = _make_js()
        nc = _make_nc(js)

        await sink.provision(nc)

        cfg = js.add_stream.call_args[0][0]
        assert cfg.retention == RetentionPolicy.LIMITS
        assert cfg.storage == StorageType.FILE
        assert cfg.max_age == 90 * 86400
        assert cfg.max_bytes == 1 * 1024**3

    async def test_provision_idempotent_when_stream_exists(self) -> None:
        """provision() calls update_stream when add_stream raises BadRequestError."""
        from nats.js.errors import BadRequestError

        sink = JetStreamAuditSink()
        js = _make_js()
        js.add_stream = AsyncMock(side_effect=BadRequestError())
        nc = _make_nc(js)

        await sink.provision(nc)

        js.update_stream.assert_awaited_once()
        assert not sink._degraded  # type: ignore[attr-defined]

    async def test_provision_logs_warning_on_config_mismatch(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """provision() logs WARNING and continues if update_stream raises."""
        from nats.js.errors import BadRequestError

        sink = JetStreamAuditSink()
        js = _make_js()
        js.add_stream = AsyncMock(side_effect=BadRequestError())
        js.update_stream = AsyncMock(side_effect=Exception("config mismatch"))
        nc = _make_nc(js)

        with caplog.at_level(logging.WARNING):
            await sink.provision(nc)  # must not raise

        assert any("config mismatch" in r.message for r in caplog.records)

    async def test_provision_marks_degraded_when_jetstream_unavailable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """provision() marks sink degraded and does not raise when JS unavailable."""
        sink = JetStreamAuditSink()
        nc = MagicMock()
        nc.jetstream.side_effect = Exception("JetStream not enabled")

        with caplog.at_level(logging.WARNING):
            await sink.provision(nc)

        assert sink._degraded  # type: ignore[attr-defined]
        assert sink._js is None  # type: ignore[attr-defined]
        assert any("AUDIT" in r.message for r in caplog.records)

    async def test_provision_add_stream_generic_error_marks_degraded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-BadRequestError from add_stream marks degraded."""
        sink = JetStreamAuditSink()
        js = _make_js()
        js.add_stream = AsyncMock(side_effect=RuntimeError("server error"))
        nc = _make_nc(js)

        with caplog.at_level(logging.WARNING):
            await sink.provision(nc)

        assert sink._degraded  # type: ignore[attr-defined]
