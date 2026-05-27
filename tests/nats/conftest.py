"""Shared fixtures for NatsBus unit tests.

Uses a real nats-server subprocess on an ephemeral port — no mocks.
nats-server binary must be in PATH (installed via ``make nats-install``,
or available as a system package). Tests that depend on the ``nc``
fixture are automatically skipped when nats-server is not found.
"""

from __future__ import annotations

from tests.factories.nats_server import (  # noqa: F401
    nats_server_url,
    nc,
    requires_nats_server,
)

__all__ = [
    "nats_server_url",
    "nc",
    "requires_nats_server",
]
