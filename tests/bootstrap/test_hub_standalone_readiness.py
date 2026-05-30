"""Structural ordering tests for _bootstrap_hub_standalone.

Tests verify that:
  1. announce_hub_ready() precedes start_readiness_responder() (#1012 invariant).
  2. ensure_stream() + ensure_kv() precede announce_hub_ready() (ADR-079 S3).

Both tests parse the AST of the function source — no I/O, no NATS.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_order(func_source: str, *call_names: str) -> dict[str, int]:
    """Return the line number of the first occurrence of each bare call name.

    Only top-level ``ast.Call`` nodes whose function is a plain ``ast.Name``
    or an ``ast.Attribute`` with the matching attr name are considered.
    Returns -1 when a name is not found.
    """
    tree = ast.parse(func_source)
    positions: dict[str, int] = {name: -1 for name in call_names}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            bare = func.id
        elif isinstance(func, ast.Attribute):
            bare = func.attr
        else:
            continue
        if bare in positions and positions[bare] == -1:
            positions[bare] = node.lineno

    return positions


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestHubStandaloneReadinessOrdering:
    def test_announce_hub_ready_called_before_start_readiness_responder(
        self,
    ) -> None:
        """announce_hub_ready() must be called before start_readiness_responder().

        Reads the source of _bootstrap_hub_standalone and asserts that
        announce_hub_ready appears earlier in the function body than
        start_readiness_responder.

        This test is RED until #1012 wires announce_hub_ready.
        """
        # Arrange — locate the source module
        hub_standalone_path = (
            Path(__file__).parents[2]
            / "src"
            / "lyra"
            / "bootstrap"
            / "standalone"
            / "hub_standalone.py"
        )
        assert hub_standalone_path.exists(), (
            f"hub_standalone.py not found at {hub_standalone_path}"
        )

        import lyra.bootstrap.standalone.hub_standalone as _mod

        func = _mod._bootstrap_hub_standalone
        func_source = inspect.getsource(func)

        # Act — find call positions relative to the function body
        positions = _call_order(
            func_source, "announce_hub_ready", "start_readiness_responder"
        )

        announce_line = positions["announce_hub_ready"]
        responder_line = positions["start_readiness_responder"]

        # Assert — announce_hub_ready must be present AND appear first
        assert announce_line != -1, (
            "announce_hub_ready() is never called in _bootstrap_hub_standalone. "
            "Wire it before start_readiness_responder (issue #1012)."
        )
        assert responder_line != -1, (
            "start_readiness_responder() not found — unexpected; check source."
        )
        assert announce_line < responder_line, (
            f"announce_hub_ready (line {announce_line}) must come before "
            f"start_readiness_responder (line {responder_line}) in "
            "_bootstrap_hub_standalone."
        )


class TestHubAudioProvisioningBeforeReady:
    """ADR-079 / #1525 S3 — ensure_stream + ensure_kv precede announce_hub_ready."""

    def test_ensure_stream_called_before_announce_hub_ready(self) -> None:
        """ensure_stream() must appear before announce_hub_ready() in hub_standalone.

        Structural assertion: parse the source of _bootstrap_hub_standalone and
        verify that ensure_stream is called before announce_hub_ready.  This
        guarantees the stream is provisioned before adapters unblock.
        """
        import lyra.bootstrap.standalone.hub_standalone as _mod

        func = _mod._bootstrap_hub_standalone
        func_source = inspect.getsource(func)

        positions = _call_order(func_source, "ensure_stream", "announce_hub_ready")

        ensure_stream_line = positions["ensure_stream"]
        announce_line = positions["announce_hub_ready"]

        assert ensure_stream_line != -1, (
            "ensure_stream() is never called in _bootstrap_hub_standalone. "
            "Hub must provision audio stream before announce_hub_ready (ADR-079 S3)."
        )
        assert announce_line != -1, (
            "announce_hub_ready() not found — unexpected; check source."
        )
        assert ensure_stream_line < announce_line, (
            f"ensure_stream (line {ensure_stream_line}) must come before "
            f"announce_hub_ready (line {announce_line}) in _bootstrap_hub_standalone."
        )

    def test_ensure_kv_called_before_announce_hub_ready(self) -> None:
        """ensure_kv() must appear before announce_hub_ready() in hub_standalone.

        Structural assertion: parse the source of _bootstrap_hub_standalone and
        verify that ensure_kv is called before announce_hub_ready.  This
        guarantees the KV bucket is provisioned before adapters unblock.
        """
        import lyra.bootstrap.standalone.hub_standalone as _mod

        func = _mod._bootstrap_hub_standalone
        func_source = inspect.getsource(func)

        positions = _call_order(func_source, "ensure_kv", "announce_hub_ready")

        ensure_kv_line = positions["ensure_kv"]
        announce_line = positions["announce_hub_ready"]

        assert ensure_kv_line != -1, (
            "ensure_kv() is never called in _bootstrap_hub_standalone. "
            "Hub must provision audio KV before announce_hub_ready (ADR-079 S3)."
        )
        assert announce_line != -1, (
            "announce_hub_ready() not found — unexpected; check source."
        )
        assert ensure_kv_line < announce_line, (
            f"ensure_kv (line {ensure_kv_line}) must come before "
            f"announce_hub_ready (line {announce_line}) in _bootstrap_hub_standalone."
        )
