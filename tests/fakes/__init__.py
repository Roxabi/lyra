"""Test fakes — minimal stand-ins for production collaborators.

Fakes may be imported by ``tests.*`` but never by ``src.lyra.*`` or production code.
"""

from __future__ import annotations

from tests.fakes.agents import FakeSTT, FakeTranscription, MockAdapter
from tests.fakes.drivers import FakeClaudeCliDriver, FakeStt, FakeTts
from tests.fakes.nats_client import FakeNatsClient
from tests.fakes.nkey_provider import FakeNkeyProvider
from tests.fakes.store import FakeStore

__all__ = [
    "FakeClaudeCliDriver",
    "FakeNatsClient",
    "FakeNkeyProvider",
    "FakeSTT",
    "FakeStt",
    "FakeStore",
    "FakeTranscription",
    "FakeTts",
    "MockAdapter",
]
