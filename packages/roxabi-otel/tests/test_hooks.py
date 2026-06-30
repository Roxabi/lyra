"""Tests for roxabi-otel lifecycle hooks."""

from __future__ import annotations

import pytest
from roxabi_otel import InMemorySpanRecorder, NoopHooks, otel_enabled, scrub_attrs

from roxabi_contracts.telemetry import ATTR_JOB_ID, ATTR_SKILL, ATTR_TRACE_ID
from roxabi_nats.adapter_base import NatsAdapterBase


class _HookAdapter(NatsAdapterBase):
    def __init__(self, hooks, **kwargs) -> None:
        super().__init__(
            subject="factory.jobs.test",
            queue_group="test-workers",
            envelope_name="JobEnvelope",
            schema_version=1,
            lifecycle_hooks=hooks,
            wait_ready=False,
            **kwargs,
        )
        self.handled = 0

    async def handle(self, msg: object, payload: dict) -> None:
        self.handled += 1

    def telemetry_attributes(self, payload: dict, result: object | None) -> dict:
        return {"roxabi.skill": "test-skill", "prompt": "secret"}


class TestOtelHooks:
    def test_in_memory_recorder_captures_wire_trace_id(self) -> None:
        recorder = InMemorySpanRecorder()
        hooks = recorder.hooks("clipool-workers")
        hooks.on_work_start(
            trace_id="550e8400-e29b-41d4-a716-446655440000",
            job_id="a" * 32,
            parent_job_id=None,
            pool_id="pool-1",
            subject="factory.jobs.claude",
            envelope_name="JobEnvelope",
            queue_group="clipool-workers",
        )
        hooks.record_domain_attrs({"roxabi.skill": "code-review"})
        hooks.on_work_end(
            trace_id="550e8400-e29b-41d4-a716-446655440000",
            job_id="a" * 32,
            duration_ms=12.5,
        )
        spans = recorder.finished_spans()
        assert len(spans) == 1
        wire_trace = "550e8400-e29b-41d4-a716-446655440000"
        assert spans[0].attributes[ATTR_TRACE_ID] == wire_trace
        assert spans[0].attributes[ATTR_JOB_ID] == "a" * 32
        assert spans[0].attributes[ATTR_SKILL] == "code-review"

    def test_scrub_drops_forbidden(self) -> None:
        cleaned, dropped = scrub_attrs({"roxabi.model": "gpt-4", "prompt": "hi"})
        assert "prompt" not in cleaned
        assert dropped == 1

    def test_otel_enabled_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_OTEL_ENABLED", "0")
        assert otel_enabled() is False


class TestAdapterHookIntegration:
    @pytest.mark.asyncio
    async def test_failing_hook_does_not_block_handle(self) -> None:
        class _BadHooks:
            def on_work_start(self, **_k: object) -> None:
                raise RuntimeError("boom")

            def on_work_end(self, **_k: object) -> None:
                raise RuntimeError("boom")

            def record_domain_attrs(self, _a: object) -> None:
                raise RuntimeError("boom")

        adapter = _HookAdapter(_BadHooks())
        msg = type("M", (), {})()
        payload = {
            "trace_id": "t1",
            "job_id": "b" * 32,
        }
        await adapter._invoke_handle_with_hooks(msg, payload)
        assert adapter.handled == 1

    @pytest.mark.asyncio
    async def test_noop_hooks(self) -> None:
        adapter = _HookAdapter(NoopHooks())
        msg = type("M", (), {})()
        payload = {
            "trace_id": "t1",
            "job_id": "c" * 32,
        }
        await adapter._invoke_handle_with_hooks(msg, payload)
        assert adapter.handled == 1