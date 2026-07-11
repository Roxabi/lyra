"""Shared fixtures for NatsBus unit tests.

Uses a real nats-server subprocess on an ephemeral port — no mocks.
nats-server binary must be in PATH (installed via ``make nats-install``,
or available as a system package). Tests that depend on the ``nc``
fixture are automatically skipped when nats-server is not found.
"""

from __future__ import annotations

import pytest

# Serialize NATS subprocess tests on one xdist worker (see test_parity_e2e.py).
pytestmark = pytest.mark.xdist_group(name="nats_server")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """conftest pytestmark does not tag child modules — apply at collection."""
    for item in items:
        if "/tests/nats/" not in str(item.fspath):
            continue
        item.add_marker(pytest.mark.subprocess_nats)
        item.add_marker(pytest.mark.xdist_group(name="nats_server"))


from tests.factories.nats_server import (  # noqa: F401,E402
    nats_server_url,
    nc,
    requires_nats_server,
)

__all__ = [
    "nats_server_url",
    "nc",
    "requires_nats_server",
]
