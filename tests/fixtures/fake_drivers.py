"""Deprecated shim — re-export moved fakes from ``tests.fakes``."""

from __future__ import annotations

from tests.fakes import FakeClaudeCliDriver, FakeStt, FakeTts

__all__ = [
    "FakeClaudeCliDriver",
    "FakeStt",
    "FakeTts",
]
