"""RED test — announce_hub_ready must be called before start_readiness_responder.

Structural assertion: parse the source of _bootstrap_hub_standalone and verify
that the call to announce_hub_ready() precedes the call to
start_readiness_responder() in the function body.

This test will FAIL until announce_hub_ready is wired into hub_standalone.py
(issue #1012).
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
