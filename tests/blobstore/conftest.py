"""Shared fixtures and helpers for tests/blobstore/."""

from __future__ import annotations

import os
import socket
from unittest.mock import AsyncMock

import pytest


def on_m1() -> bool:
    """True iff we appear to be running on M1 (roxabituwer)."""
    return socket.gethostname() == "roxabituwer" or os.environ.get("LYRA_HOST") == "m1"


@pytest.fixture(autouse=True)
def _patch_nats_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent build_app from attempting real NATS connect in blobstore tests.

    Without this, _connect_nats() follows NATS_URL in the environment and
    _provision_nats() times out on missing JetStream permissions (~5 s).
    Tests that explicitly inject a mock NATS client pass ``nats=`` directly
    to build_app and are unaffected by this patch.
    """
    monkeypatch.setattr(
        "lyra.blobstore.serve._connect_nats",
        AsyncMock(return_value=None),
    )
