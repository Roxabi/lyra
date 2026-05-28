"""Tests for deploy/nats/bootstrap_streams.py — stream provisioning."""

from __future__ import annotations

from deploy.nats.bootstrap_streams import STREAMS


def test_lyra_outbound_stream_exists() -> None:
    """lyra-outbound stream must be present in the bootstrap STREAMS map."""
    assert "lyra-outbound" in STREAMS
