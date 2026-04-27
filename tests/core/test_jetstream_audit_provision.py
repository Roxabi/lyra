"""Tests for JetStreamAuditSink.provision() (#855) — S3 criteria.

Subject routing tests for #942 (skip_permissions split) are in
TestJetStreamAuditSinkSubjectRouting at the bottom of this file.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.infrastructure.audit.jetstream_sink import (
    _SUBJECT_NORMAL,
    _SUBJECT_PREFIX,
    _SUBJECT_PRIVILEGED,
    JetStreamAuditSink,
)


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
        """provision() logs WARNING and marks degraded if update_stream raises."""
        from nats.js.errors import BadRequestError

        sink = JetStreamAuditSink()
        js = _make_js()
        js.add_stream = AsyncMock(side_effect=BadRequestError())
        js.update_stream = AsyncMock(side_effect=Exception("arbitrary error"))
        nc = _make_nc(js)

        with caplog.at_level(logging.WARNING):
            await sink.provision(nc)  # must not raise

        assert any("AUDIT: stream config mismatch" in r.message for r in caplog.records)
        assert sink._degraded  # type: ignore[attr-defined]

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


def _make_event(*, skip_permissions: bool) -> object:
    """Build a minimal SecurityEvent for emit() tests."""
    from roxabi_contracts.audit import SecurityEvent
    from roxabi_contracts.envelope import CONTRACT_VERSION

    return SecurityEvent(
        contract_version=CONTRACT_VERSION,
        trace_id="t-test",
        issued_at=datetime.now(UTC),
        kind="cli.subprocess.spawned",
        pool_id="p:1",
        agent_name="bot",
        skip_permissions=skip_permissions,
        tools_restricted=False,
        tools_allowlist=[],
        model="claude-opus-4-6",
        pid=999,
    )


class TestJetStreamAuditSinkSubjectRouting:
    """#942 — skip_permissions routes to dedicated NATS subjects."""

    async def test_emit_privileged_subject_when_skip_permissions_true(self) -> None:
        """emit() uses lyra.audit.security.privileged when skip_permissions=True."""
        import json

        sink = JetStreamAuditSink()
        js = _make_js()
        js.publish = AsyncMock()
        sink._js = js  # type: ignore[attr-defined]

        event = _make_event(skip_permissions=True)
        await sink.emit(event)  # type: ignore[arg-type]

        js.publish.assert_awaited_once()
        subject, payload = js.publish.call_args[0]
        assert subject == _SUBJECT_PRIVILEGED
        assert json.loads(payload)["skip_permissions"] is True

    async def test_emit_normal_subject_when_skip_permissions_false(self) -> None:
        """emit() uses lyra.audit.security.normal when skip_permissions=False."""
        import json

        sink = JetStreamAuditSink()
        js = _make_js()
        js.publish = AsyncMock()
        sink._js = js  # type: ignore[attr-defined]

        event = _make_event(skip_permissions=False)
        await sink.emit(event)  # type: ignore[arg-type]

        js.publish.assert_awaited_once()
        subject, payload = js.publish.call_args[0]
        assert subject == _SUBJECT_NORMAL
        assert json.loads(payload)["skip_permissions"] is False

    def test_subjects_are_under_lyra_audit_wildcard(self) -> None:
        """Both subjects fall under _SUBJECT_PREFIX.* (stream wildcard)."""
        assert _SUBJECT_PRIVILEGED.startswith(f"{_SUBJECT_PREFIX}.")
        assert _SUBJECT_NORMAL.startswith(f"{_SUBJECT_PREFIX}.")

    async def test_emit_degraded_sink_does_not_publish_privileged_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Degraded sink logs (not publishes) privileged events; subject in message."""
        sink = JetStreamAuditSink()
        sink._degraded = True  # type: ignore[attr-defined]
        js = _make_js()
        js.publish = AsyncMock()
        sink._js = js  # type: ignore[attr-defined]

        event = _make_event(skip_permissions=True)
        with caplog.at_level(logging.WARNING, logger="lyra.security"):
            await sink.emit(event)  # type: ignore[arg-type]

        js.publish.assert_not_awaited()
        security_records = [r for r in caplog.records if r.name == "lyra.security"]
        assert len(security_records) == 1
        assert _SUBJECT_PRIVILEGED in security_records[0].message
