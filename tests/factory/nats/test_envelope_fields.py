"""Tests for hub work-path envelope field SSoT (#2069)."""

from __future__ import annotations

import json

import pytest

from factory.core.trace import TraceContext
from factory.nats.audio.nats_tts_codec import TtsCodec
from factory.nats.envelope_fields import mint_work_envelope_fields, wire_trace_id_hex
from factory.nats.image.nats_image_codec import ImageCodec, ImageGenParams
from factory.nats.stt.nats_stt_codec import SttCodec, SttEncodeParams
from roxabi_contracts import BlobRef

_TRACE = "550e8400-e29b-41d4-a716-446655440000"
_JOB = "a" * 32
_POOL = "pool:tg:chat:1"
_BLOB = BlobRef(
    store_key="k", content_hash="abc", mime="audio/ogg", size=1, source="t"
)


class TestMintWorkEnvelopeFields:
    def test_explicit_args(self) -> None:
        fields = mint_work_envelope_fields(
            trace_id=_TRACE, job_id=_JOB, pool_id=_POOL
        )
        assert fields.trace_id == _TRACE
        assert fields.job_id == _JOB
        assert fields.pool_id == _POOL

    def test_reads_trace_context(self) -> None:
        tok_t = TraceContext.set_trace_id(_TRACE)
        tok_p = TraceContext.set_pool_id(_POOL)
        try:
            fields = mint_work_envelope_fields(job_id=_JOB)
            assert fields.trace_id == _TRACE
            assert fields.pool_id == _POOL
        finally:
            TraceContext.reset_pool_id(tok_p)
            TraceContext.reset_trace_id(tok_t)

    def test_reads_root_job_id_from_trace_context(self) -> None:
        tok_t = TraceContext.set_trace_id(_TRACE)
        tok_j = TraceContext.set_root_job_id(_JOB)
        try:
            fields = mint_work_envelope_fields()
            assert fields.job_id == _JOB
        finally:
            TraceContext.reset_root_job_id(tok_j)
            TraceContext.reset_trace_id(tok_t)

    def test_missing_trace_raises(self, _default_trace_context) -> None:
        TraceContext.reset_trace_id(_default_trace_context)
        with pytest.raises(ValueError, match="trace_id required"):
            mint_work_envelope_fields(job_id=_JOB)

    def test_wire_trace_id_hex_from_context(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            assert wire_trace_id_hex() == _TRACE.replace("-", "")
        finally:
            TraceContext.reset_trace_id(tok)


class TestHubCodecsUseTraceContext:
    def test_stt_encode_propagates_trace_id(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            payload = SttCodec().encode(
                _BLOB, "audio/ogg", SttEncodeParams(), job_id=_JOB
            )
            data = json.loads(payload)
            assert data["trace_id"] == _TRACE
            assert data["job_id"] == _JOB
        finally:
            TraceContext.reset_trace_id(tok)

    def test_tts_encode_propagates_trace_id(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            payload = TtsCodec().encode("hi", job_id=_JOB)
            data = json.loads(payload)
            assert data["trace_id"] == _TRACE
            assert data["job_id"] == _JOB
        finally:
            TraceContext.reset_trace_id(tok)

    def test_image_encode_propagates_trace_id(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            payload = ImageCodec().encode(
                "cat", "flux", ImageGenParams(), job_id=_JOB
            )
            data = json.loads(payload)
            assert data["trace_id"] == _TRACE
            assert data["job_id"] == _JOB
        finally:
            TraceContext.reset_trace_id(tok)
