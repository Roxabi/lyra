"""Structural and behavioral ordering tests for _bootstrap_hub_standalone.

Tests verify that:
  1. announce_hub_ready() precedes start_readiness_responder() (#1012 invariant).
  2. ensure_stream() + ensure_kv() precede announce_hub_ready() (ADR-079 S3).

AST tests (class TestHub*) parse source — no I/O, no NATS.
Behavioral tests (class TestHubAudioProvisioningBehavioral) use recording mocks
to assert actual await order at runtime, complementing the AST tests which operate
on static source and cannot assert that calls are actually awaited.

NOTE on AST test limitation: AST tests in TestHubAudioProvisioningBeforeReady parse
dead/guarded code (function bodies with conditional branches) and verify textual
ordering, but they cannot verify that the calls are actually awaited in the correct
order at runtime. The behavioral companion tests below close this gap.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tests.helpers.hub_standalone_bootstrap import (
    hub_test_config,
    make_hub_stubs,
    stub_hub_bootstrap,
)

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
            / "factory"
            / "bootstrap"
            / "standalone"
            / "hub_standalone.py"
        )
        assert hub_standalone_path.exists(), (
            f"hub_standalone.py not found at {hub_standalone_path}"
        )

        import factory.bootstrap.standalone.hub_standalone as _mod

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
        import factory.bootstrap.standalone.hub_standalone as _mod

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

        NOTE: This AST test verifies textual ordering of call sites but cannot
        assert that the calls are actually awaited. See
        TestHubAudioProvisioningBehavioral for the runtime companion.
        """
        import factory.bootstrap.standalone.hub_standalone as _mod

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


# ---------------------------------------------------------------------------
# Behavioral companion — runtime ordering (B3, ADR-079 S3)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402 — kept below class definitions to match module style


class TestHubAudioProvisioningBehavioral:
    """Behavioral ordering: ensure_stream + ensure_kv awaited before announce_hub_ready.

    These tests run the actual _bootstrap_hub_standalone function with deep mocking
    of all infrastructure dependencies, stopping execution after announce_hub_ready
    via a side_effect sentinel. They complement the AST tests in
    TestHubAudioProvisioningBeforeReady which only verify textual call ordering.
    """

    @pytest.mark.asyncio
    async def test_ensure_stream_and_kv_awaited_before_announce_hub_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_stream and ensure_kv awaited before announce_hub_ready (ADR-079 S3).

        Records the call order of ensure_stream, ensure_kv, and announce_hub_ready
        via side_effect callbacks, then asserts the ordering invariant.
        The test stops immediately after announce_hub_ready to avoid running the
        full hub lifecycle.
        """
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        raw_config = hub_test_config()
        mock_nc, fake_open_stores = make_hub_stubs()
        mock_nc.jetstream.return_value = MagicMock(
            add_stream=AsyncMock(), update_stream=AsyncMock()
        )

        call_order: list[str] = []

        async def _record_announce_hub_ready(*_a, **_kw):
            call_order.append("announce_hub_ready")
            raise SystemExit("test-sentinel: stop after announce_hub_ready")

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        stub_hub_bootstrap(
            monkeypatch,
            mock_nc,
            fake_open_stores,
            call_order=call_order,
            announce_hub_ready=_record_announce_hub_ready,
        )

        with pytest.raises(SystemExit, match="test-sentinel"):
            await _bootstrap_hub_standalone(raw_config)

        assert "ensure_stream" in call_order, (
            "ensure_stream was never awaited — hub must provision audio stream "
            "before announce_hub_ready (ADR-079 S3)."
        )
        assert "ensure_kv" in call_order, (
            "ensure_kv was never awaited — hub must provision audio KV "
            "before announce_hub_ready (ADR-079 S3)."
        )
        assert "announce_hub_ready" in call_order, (
            "announce_hub_ready was never called — unexpected."
        )
        es_idx = call_order.index("ensure_stream")
        ev_idx = call_order.index("ensure_kv")
        ar_idx = call_order.index("announce_hub_ready")
        assert es_idx < ar_idx, (
            f"ensure_stream (pos {es_idx}) must precede announce_hub_ready "
            f"(pos {ar_idx}) — ADR-079 S3 ordering violated."
        )
        assert ev_idx < ar_idx, (
            f"ensure_kv (pos {ev_idx}) must precede announce_hub_ready "
            f"(pos {ar_idx}) — ADR-079 S3 ordering violated."
        )
        assert "ensure_active_jobs_kv" in call_order, (
            "ensure_active_jobs_kv was never awaited — hub must provision the "
            "active-jobs registry KV before announce_hub_ready (ADR-079 S3)."
        )
        ej_idx = call_order.index("ensure_active_jobs_kv")
        assert ej_idx < ar_idx, (
            f"ensure_active_jobs_kv (pos {ej_idx}) must precede announce_hub_ready "
            f"(pos {ar_idx}) — ADR-079 S3 ordering violated."
        )
        assert "publish_bot_roster" in call_order, (
            "publish_bot_roster was never awaited — hub must publish roster "
            "before announce_hub_ready (#1946)."
        )
        pbr_idx = call_order.index("publish_bot_roster")
        assert pbr_idx < ar_idx, (
            f"publish_bot_roster (pos {pbr_idx}) must precede announce_hub_ready "
            f"(pos {ar_idx}) — #1946 ordering violated."
        )

    @pytest.mark.asyncio
    async def test_announce_hub_ready_not_called_when_ensure_stream_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """announce_hub_ready not called when ensure_stream raises nats.errors.Error.

        This tests S1 fail-fast: provisioning failure is terminal. The hub must not
        signal readiness if stream provisioning fails, because adapters would then
        unblock and attempt to bind a non-existent stream/KV.
        """
        import nats.errors

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        raw_config = hub_test_config()
        mock_nc, fake_open_stores = make_hub_stubs()

        mock_announce = AsyncMock()

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        stub_hub_bootstrap(
            monkeypatch,
            mock_nc,
            fake_open_stores,
            ensure_stream=nats.errors.Error("stream create denied"),
            announce_hub_ready=mock_announce,
        )

        with pytest.raises(nats.errors.Error):
            await _bootstrap_hub_standalone(raw_config)

        # announce_hub_ready must NOT be called when provisioning fails (ADR-079 S3 S1).
        mock_announce.assert_not_awaited()
