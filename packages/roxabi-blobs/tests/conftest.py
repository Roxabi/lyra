"""Shared fixtures for `roxabi_blobs` tests.

`store` yields an opened `FsBlobStore` rooted at a tmp_path. Single-host,
no NATS, no network — pure FS + SQLite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from roxabi_blobs import FsBlobStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[FsBlobStore]:
    async with FsBlobStore(tmp_path) as s:
        yield s


@pytest.fixture
def sample_bytes() -> bytes:
    return b"the quick brown fox jumps over the lazy dog"
