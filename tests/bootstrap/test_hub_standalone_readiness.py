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
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_hub_stubs() -> tuple:
    """Return (mock_nc, fake_open_stores) for hub bootstrap short-circuit tests."""
    mock_nc = AsyncMock()
    mock_nc.is_connected = True
    mock_nc.close = AsyncMock()
    mock_nc.drain = AsyncMock()
    mock_js = MagicMock()
    mock_nc.jetstream = MagicMock(return_value=mock_js)

    @asynccontextmanager
    async def _fake_open_stores(*_args, **_kwargs):
        # Minimal stores stub; hub_standalone destructures the result
        stores = MagicMock()
        stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
        stores.auth = MagicMock()
        stores.bot = MagicMock()
        stores.identity_alias = MagicMock()
        stores.agent = MagicMock()
        yield stores

    return mock_nc, _fake_open_stores


def _test_config() -> dict:
    return {
        "defaults": {"cwd": "/tmp"},
        "admin": {"user_ids": ["test_admin"]},
        "telegram": {"bots": []},
        "discord": {"bots": []},
        "auth": {"telegram_bots": [], "discord_bots": []},
        "message_index": {},
    }


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
        raw_config = _test_config()
        mock_nc, fake_open_stores = _make_hub_stubs()

        call_order: list[str] = []

        async def _record_ensure_stream(*_a, **_kw):
            call_order.append("ensure_stream")

        async def _record_ensure_kv(*_a, **_kw):
            call_order.append("ensure_kv")
            return MagicMock()

        async def _record_announce_hub_ready(*_a, **_kw):
            call_order.append("announce_hub_ready")
            raise SystemExit("test-sentinel: stop after announce_hub_ready")

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        with (
            patch(
                "factory.bootstrap.standalone.hub_standalone.nats_connect",
                AsyncMock(return_value=mock_nc),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.acquire_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.release_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.open_stores",
                fake_open_stores,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.seed_grants_from_bots",
                AsyncMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_bot_auths",
                return_value=(MagicMock(), [], [], []),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._resolve_bot_agent_map",
                AsyncMock(return_value={}),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.load_agent_configs",
                return_value={"default": MagicMock()},
            ),
            # _load_messages is lazily imported; patch at the source module.
            patch(
                "factory.bootstrap.factory.config._load_messages",
                return_value=MagicMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_pairing_manager",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._build_hub_and_wire",
                AsyncMock(
                    return_value=(
                        MagicMock(
                            inbound_bus=AsyncMock(start=AsyncMock()),
                        ),
                        [],
                        [],
                        MagicMock(),
                        MagicMock(),
                    )
                ),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.start_mint_failure_subscriber",
                AsyncMock(return_value=MagicMock()),
            ),
            # ensure_stream/ensure_kv are lazily imported inside the function body;
            # patch at source module path so the local import picks up the stub.
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_stream",
                side_effect=_record_ensure_stream,
            ),
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_kv",
                side_effect=_record_ensure_kv,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.announce_hub_ready",
                side_effect=_record_announce_hub_ready,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.log_contracts_version",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_inbound_bus",
                return_value=(AsyncMock(), MagicMock()),
            ),
        ):
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
        raw_config = _test_config()
        mock_nc, fake_open_stores = _make_hub_stubs()

        mock_announce = AsyncMock()

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        with (
            patch(
                "factory.bootstrap.standalone.hub_standalone.nats_connect",
                AsyncMock(return_value=mock_nc),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.acquire_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.release_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.open_stores",
                fake_open_stores,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.seed_grants_from_bots",
                AsyncMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_bot_auths",
                return_value=(MagicMock(), [], [], []),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._resolve_bot_agent_map",
                AsyncMock(return_value={}),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.load_agent_configs",
                return_value={"default": MagicMock()},
            ),
            # _load_messages is lazily imported; patch at the source module.
            patch(
                "factory.bootstrap.factory.config._load_messages",
                return_value=MagicMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_pairing_manager",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._build_hub_and_wire",
                AsyncMock(
                    return_value=(
                        MagicMock(
                            inbound_bus=AsyncMock(start=AsyncMock()),
                        ),
                        [],
                        [],
                        MagicMock(),
                        MagicMock(),
                    )
                ),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.start_mint_failure_subscriber",
                AsyncMock(return_value=MagicMock()),
            ),
            # ensure_stream/ensure_kv are lazily imported; patch at source module.
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_stream",
                side_effect=nats.errors.Error("stream create denied"),
            ),
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_kv",
                AsyncMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.announce_hub_ready",
                mock_announce,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.log_contracts_version",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_inbound_bus",
                return_value=(AsyncMock(), MagicMock()),
            ),
        ):
            with pytest.raises(nats.errors.Error):
                await _bootstrap_hub_standalone(raw_config)

        # announce_hub_ready must NOT be called when provisioning fails (ADR-079 S3 S1).
        mock_announce.assert_not_awaited()
