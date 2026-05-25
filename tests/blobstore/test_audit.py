"""RED-phase tests: BlobAuditEvent shape, BlobAuditSink, KV readiness (S3)."""

from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# lyra.blobstore.audit_sink does not exist yet — RED until T13 lands.
from lyra.blobstore.audit_sink import (
    BlobAuditSink,  # type: ignore[import-not-found]  # noqa: F401
)

from roxabi_contracts.audit.blobs import BlobAuditEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _make_event(**kwargs: object) -> BlobAuditEvent:
    """Return a minimal valid BlobAuditEvent with sensible defaults."""
    defaults: dict = {
        "contract_version": "1",
        "trace_id": "trace-abc",
        "issued_at": _NOW,
        "op": "put",
        "result": "ok",
        "store_key": "sha256/ab/cd/ef",
        "content_hash": "abcdef1234567890",
        "size": 1024,
        "source": "lyra-telegram",
    }
    defaults.update(kwargs)
    return BlobAuditEvent(**defaults)


# ---------------------------------------------------------------------------
# K1 — BlobAuditEvent contract shape
# ---------------------------------------------------------------------------


class TestBlobAuditEventShape:
    """BlobAuditEvent inherits ContractEnvelope and carries V8-required fields."""

    def test_blob_audit_event_subject_is_lyra_audit_blobs_op(self) -> None:
        """subject_for(op) returns the canonical lyra.audit.blobs.{op} NATS subject."""
        assert BlobAuditEvent.subject_for(op="put") == "lyra.audit.blobs.put"
        assert BlobAuditEvent.subject_for(op="get") == "lyra.audit.blobs.get"
        assert BlobAuditEvent.subject_for(op="exists") == "lyra.audit.blobs.exists"
        assert BlobAuditEvent.subject_for(op="delete") == "lyra.audit.blobs.delete"

    def test_blob_audit_event_has_required_fields(self) -> None:
        """Instantiating BlobAuditEvent with all spec-mandated fields succeeds."""
        event = _make_event()

        # ContractEnvelope base fields
        assert event.contract_version == "1"
        assert event.trace_id == "trace-abc"
        assert event.issued_at == _NOW

        # Blobs-domain fields
        assert event.op == "put"
        assert event.result == "ok"
        assert event.store_key == "sha256/ab/cd/ef"
        assert event.content_hash == "abcdef1234567890"
        assert event.size == 1024
        assert event.source == "lyra-telegram"

    def test_blob_audit_event_optional_fields_accept_none(self) -> None:
        """store_key, content_hash, size, source are all nullable."""
        event = _make_event(
            store_key=None,
            content_hash=None,
            size=None,
            source=None,
        )
        assert event.store_key is None
        assert event.content_hash is None
        assert event.size is None
        assert event.source is None

    def test_blob_audit_event_op_literals(self) -> None:
        """op field accepts exactly the four Protocol method names."""
        for op in ("put", "get", "exists", "delete"):
            event = _make_event(op=op)
            assert event.op == op

    def test_blob_audit_event_result_literals(self) -> None:
        """result field accepts the spec-defined result codes."""
        for result in (
            "ok",
            "not_found",
            "unauthorized",
            "write_failed",
            "internal_error",
        ):
            event = _make_event(result=result)
            assert event.result == result


# ---------------------------------------------------------------------------
# S2 — BlobAuditSink publishes to correct NATS subject
# ---------------------------------------------------------------------------


class TestBlobAuditSinkPublish:
    """BlobAuditSink.emit() publishes to lyra.audit.blobs.{op} via JetStream."""

    async def test_blob_audit_sink_publishes_to_correct_subject(self) -> None:
        """emit(event) calls js.publish with subject=lyra.audit.blobs.{op}."""
        js = AsyncMock()
        js.publish = AsyncMock()

        sink = BlobAuditSink()
        # Inject a mocked JetStream (simulating post-provision state)
        sink._js = js
        sink._degraded = False

        event = _make_event(op="put")
        await sink.emit(event)

        js.publish.assert_awaited_once()
        call_args = js.publish.call_args
        subject_used = (
            call_args.args[0] if call_args.args else call_args.kwargs.get("subject")
        )
        assert subject_used == "lyra.audit.blobs.put"

        payload_used = (
            call_args.args[1]
            if len(call_args.args) > 1
            else call_args.kwargs.get("payload")
        )
        assert payload_used is not None
        # Round-trip: published bytes must deserialize to the event fields
        recovered = json.loads(payload_used.decode())
        assert recovered["op"] == "put"
        assert recovered["result"] == "ok"

    async def test_blob_audit_sink_publishes_correct_subject_for_get(self) -> None:
        """Subject varies per op — get -> lyra.audit.blobs.get."""
        js = AsyncMock()
        js.publish = AsyncMock()
        sink = BlobAuditSink()
        sink._js = js
        sink._degraded = False

        await sink.emit(_make_event(op="get"))

        subject_used = js.publish.call_args.args[0]
        assert subject_used == "lyra.audit.blobs.get"


# ---------------------------------------------------------------------------
# S2 — BlobAuditSink degradation semantics
# ---------------------------------------------------------------------------


class TestBlobAuditSinkDegradation:
    """BlobAuditSink degrades to lyra.security logger when NATS publish fails."""

    async def test_blob_audit_sink_degrades_on_publish_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """After a publish error _degraded=True; next emits to lyra.security."""
        import nats.errors

        js = AsyncMock()
        js.publish = AsyncMock(side_effect=nats.errors.Error("timeout"))

        sink = BlobAuditSink()
        sink._js = js
        sink._degraded = False

        with caplog.at_level(logging.WARNING, logger="lyra.security"):
            await sink.emit(_make_event(op="put"))

        # After the first failure the sink must be degraded
        assert sink._degraded is True

        # Reset mock so we can detect if it's (incorrectly) called again
        js.publish = AsyncMock()
        with caplog.at_level(logging.WARNING, logger="lyra.security"):
            await sink.emit(_make_event(op="get"))

        # NATS publish must NOT have been called on the second emit
        js.publish.assert_not_awaited()
        # lyra.security must have received at least one record
        security_records = [r for r in caplog.records if r.name == "lyra.security"]
        assert len(security_records) >= 1

    async def test_blob_audit_sink_uses_security_logger_when_nats_is_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """BlobAuditSink with no JetStream wired logs to lyra.security on emit."""
        sink = BlobAuditSink()
        # _js is None, _degraded is False — None path should still go to logger

        with caplog.at_level(logging.WARNING, logger="lyra.security"):
            await sink.emit(_make_event(op="delete"))

        security_records = [r for r in caplog.records if r.name == "lyra.security"]
        assert len(security_records) >= 1
        # The log message must contain the serialized event payload
        full_text = security_records[0].getMessage()
        assert "delete" in full_text


# ---------------------------------------------------------------------------
# S3 — KV readiness announce after startup
# ---------------------------------------------------------------------------


class TestBlobstoreKVReadiness:
    """After build_app startup, blobstore.ready=b'true' is written to lyra-state KV."""

    async def test_blobstore_ready_kv_announce_after_startup(
        self, tmp_path: pathlib.Path
    ) -> None:
        """build_app(nats=<mock>) triggers kv.put('blobstore.ready', b'true').

        RED until T13 wires the nats= kwarg into build_app lifespan.
        Expected failure: TypeError: unexpected keyword argument 'nats'
        """
        import httpx

        from lyra.blobstore.serve import build_app

        blob_root = tmp_path / "blobs"
        blob_root.mkdir()

        mock_kv = AsyncMock()
        mock_kv.put = AsyncMock()

        mock_js = AsyncMock()
        mock_js.key_value = AsyncMock(return_value=mock_kv)
        # Also cover the stream-ensure path on provision
        mock_js.add_stream = AsyncMock()

        mock_nc = MagicMock()
        mock_nc.jetstream = MagicMock(return_value=mock_js)

        # T13 adds nats= kwarg; until then this call raises TypeError (RED)
        app = build_app(
            token="test-token",
            blob_root=blob_root,
            nats=mock_nc,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

        # After lifespan, KV bucket must have received the readiness signal
        mock_kv.put.assert_awaited_once_with("blobstore.ready", b"true")
