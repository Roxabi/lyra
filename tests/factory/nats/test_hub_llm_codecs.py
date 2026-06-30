"""LLM codec trace propagation — hub work paths (#2069 Block 9 P0)."""

from __future__ import annotations

import json

from factory.core.trace import TraceContext
from factory.llm.cli_nats_codec import CliNatsCodec
from factory.llm.cli_pool_codec import CliPoolCodec

_TRACE = "550e8400-e29b-41d4-a716-446655440000"
_JOB = "a" * 32
_POOL = "pool:tg:chat:1"


class _ModelCfg:
    model = "claude-sonnet"
    max_tokens = 1024
    temperature = 0.0

    def model_dump(self) -> dict:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


class TestLlmCodecsUseTraceContext:
    def test_cli_pool_encode_propagates_trace_id(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            payload, trace_id = CliPoolCodec().encode(
                "hi",
                _ModelCfg(),
                "sys",
                None,
                stream=False,
                pool_id=_POOL,
            )
            data = json.loads(payload)
            assert trace_id == _TRACE
            assert data["trace_id"] == _TRACE
            assert data["pool_id"] == _POOL
        finally:
            TraceContext.reset_trace_id(tok)

    def test_cli_nats_encode_propagates_trace_id_and_job(self) -> None:
        tok = TraceContext.set_trace_id(_TRACE)
        try:
            payload, trace_id = CliNatsCodec().encode(
                "hi",
                _ModelCfg(),
                "sys",
                None,
                stream=False,
                root_job_id=_JOB,
                pool_id=_POOL,
            )
            data = json.loads(payload)
            assert trace_id == _TRACE
            assert data["trace_id"] == _TRACE
            assert data["job_id"] == _JOB
        finally:
            TraceContext.reset_trace_id(tok)