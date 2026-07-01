"""Tests for factory OTel hook wiring."""

from __future__ import annotations

from roxabi_otel import NoopHooks

from factory.obs.otel_wiring import build_lifecycle_hooks


def test_disabled_flag_returns_noop(monkeypatch) -> None:
    monkeypatch.setenv("ROXABI_OTEL_ENABLED", "0")
    hooks = build_lifecycle_hooks("clipool-workers")
    assert isinstance(hooks, NoopHooks)