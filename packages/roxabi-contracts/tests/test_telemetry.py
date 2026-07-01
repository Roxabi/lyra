"""Tests for roxabi_contracts.telemetry registry and hooks protocol."""

from __future__ import annotations

from roxabi_contracts.telemetry import (
    ATTR_JOB_ID,
    ATTR_SKILL,
    ATTR_SKILL_UNKNOWN,
    FORBIDDEN_ATTR_PREFIXES,
    FORBIDDEN_ATTRS,
    MessageLifecycleHooks,
)


def test_attr_registry_constants() -> None:
    assert ATTR_JOB_ID == "roxabi.job_id"
    assert ATTR_SKILL_UNKNOWN == "unknown"
    assert "prompt" in FORBIDDEN_ATTRS
    assert "user." in FORBIDDEN_ATTR_PREFIXES[0]


class _StubHooks:
    def on_work_start(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def on_work_end(self, **kwargs: object) -> None:
        self.end_kwargs = kwargs

    def record_domain_attrs(self, job_id: str, attrs: dict) -> None:
        self.job_id = job_id
        self.attrs = attrs


def test_hooks_protocol_runtime_checkable() -> None:
    stub = _StubHooks()
    assert isinstance(stub, MessageLifecycleHooks)
    stub.record_domain_attrs("a" * 32, {ATTR_SKILL: "x"})
    assert stub.job_id == "a" * 32