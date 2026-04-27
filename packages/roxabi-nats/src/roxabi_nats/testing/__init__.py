"""roxabi_nats.testing — NATS test doubles for Lyra domain contracts.

Provides FakeTtsWorker, FakeSttWorker, and FakeImageWorker: in-process NATS
subscribers that stand in for real workers during integration tests. Moved
from roxabi_contracts.{voice,image}.testing per ADR-059 V6.

Install with: uv pip install roxabi-nats[testing]
or inside the workspace: uv sync --all-extras

Imports are lazy — loading this module does NOT trigger scipy or nats-py.
Import the submodule directly or use attribute access:
    from roxabi_nats.testing.voice import FakeTtsWorker
    from roxabi_nats.testing import FakeTtsWorker  # lazy, same cost
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from roxabi_nats.testing.image import FakeImageWorker
    from roxabi_nats.testing.voice import FakeSttWorker, FakeTtsWorker

__all__: list[str] = ["FakeTtsWorker", "FakeSttWorker", "FakeImageWorker"]

_VOICE = ("FakeTtsWorker", "FakeSttWorker")
_IMAGE = ("FakeImageWorker",)


@overload
def __getattr__(name: Literal["FakeTtsWorker"]) -> type[FakeTtsWorker]: ...


@overload
def __getattr__(name: Literal["FakeSttWorker"]) -> type[FakeSttWorker]: ...


@overload
def __getattr__(name: Literal["FakeImageWorker"]) -> type[FakeImageWorker]: ...


@overload
def __getattr__(name: str) -> object: ...


def __getattr__(name: str) -> object:
    if name in _VOICE:
        from roxabi_nats.testing.voice import FakeSttWorker, FakeTtsWorker

        return FakeTtsWorker if name == "FakeTtsWorker" else FakeSttWorker
    if name in _IMAGE:
        from roxabi_nats.testing.image import FakeImageWorker

        return FakeImageWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
