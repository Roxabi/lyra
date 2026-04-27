"""roxabi_nats.testing — NATS test doubles for Lyra domain contracts.

Provides FakeTtsWorker, FakeSttWorker, and FakeImageWorker: in-process NATS
subscribers that stand in for real workers during integration tests. Moved
from roxabi_contracts.{voice,image}.testing per ADR-059 V6.

Install with: uv pip install roxabi-nats[testing]
or inside the workspace: uv sync --all-extras
"""

from roxabi_nats.testing.image import FakeImageWorker
from roxabi_nats.testing.voice import FakeSttWorker, FakeTtsWorker

__all__: list[str] = ["FakeTtsWorker", "FakeSttWorker", "FakeImageWorker"]
