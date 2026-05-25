"""Shared fixtures and helpers for tests/blobstore/."""

from __future__ import annotations

import os
import socket


def on_m1() -> bool:
    """True iff we appear to be running on M1 (roxabituwer)."""
    return (
        socket.gethostname() == "roxabituwer"
        or os.environ.get("LYRA_HOST") == "m1"
    )
