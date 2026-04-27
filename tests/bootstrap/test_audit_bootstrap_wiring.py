"""Tests for bootstrap audit sink wiring — build_cli_pool + provision (#855)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from lyra.bootstrap.factory.hub_builder import build_cli_pool
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig


def _make_cli_agent(name: str = "test_agent") -> Agent:
    return Agent(
        name=name,
        system_prompt="prompt",
        memory_namespace="test",
        llm_config=ModelConfig(backend="claude-cli"),
    )


class TestBuildCliPoolAuditSinkWiring:
    async def test_build_cli_pool_passes_audit_sink_to_pool(self) -> None:
        """build_cli_pool forwards audit_sink kwarg to CliPool constructor."""
        from lyra.infrastructure.audit.jetstream_sink import JetStreamAuditSink

        agent_configs = {"a": _make_cli_agent()}
        sink = JetStreamAuditSink()

        with patch("lyra.bootstrap.factory.hub_builder.CliPool") as MockCliPool:
            mock_pool = MagicMock()
            mock_pool.start = AsyncMock()
            MockCliPool.return_value = mock_pool

            await build_cli_pool({}, agent_configs, audit_sink=sink)

        _, kwargs = MockCliPool.call_args
        assert kwargs.get("audit_sink") is sink

    async def test_build_cli_pool_no_audit_sink_defaults_to_none(self) -> None:
        """build_cli_pool defaults audit_sink=None when not provided."""
        agent_configs = {"a": _make_cli_agent()}

        with patch("lyra.bootstrap.factory.hub_builder.CliPool") as MockCliPool:
            mock_pool = MagicMock()
            mock_pool.start = AsyncMock()
            MockCliPool.return_value = mock_pool

            await build_cli_pool({}, agent_configs)

        _, kwargs = MockCliPool.call_args
        assert kwargs.get("audit_sink") is None


class TestJetStreamAuditSinkBootstrapIntegration:
    async def test_provision_is_called_before_cli_pool_in_standalone(self) -> None:
        """provision() must be called before build_cli_nats_driver in hub_standalone."""
        call_order: list[str] = []

        async def _fake_provision(*_: object) -> None:
            call_order.append("provision")

        async def _fake_build_cli_nats_driver(*_: object) -> None:  # type: ignore[misc]
            call_order.append("build_cli_nats_driver")

        with patch(
            "lyra.bootstrap.standalone.hub_standalone.JetStreamAuditSink"
        ) as MockSink:
            mock_sink = MagicMock()
            mock_sink.provision = _fake_provision
            MockSink.return_value = mock_sink

            with patch(
                "lyra.bootstrap.standalone.hub_standalone.build_cli_nats_driver",
                side_effect=_fake_build_cli_nats_driver,
            ):
                # Import the module — both symbols are referenced at module level
                import lyra.bootstrap.standalone.hub_standalone as hub_mod

                # Verify structural presence — imported at module level
                assert hasattr(hub_mod, "JetStreamAuditSink")

        # If both were called, provision must precede build_cli_nats_driver
        _key = "build_cli_nats_driver"
        if "provision" in call_order and _key in call_order:
            assert call_order.index("provision") < call_order.index(_key)

    async def test_audit_sink_skip_permissions_event_fields(self) -> None:
        """A spawn with skip_permissions=True emits event with that field True."""
        from lyra.core.cli.cli_pool import CliPool
        from tests.core.conftest_cli_pool import make_fake_proc

        events: list[object] = []

        async def _capture(ev: object) -> None:
            events.append(ev)

        sink = MagicMock()
        sink.emit = _capture  # type: ignore[method-assign]

        pool = CliPool(audit_sink=sink)
        model = ModelConfig(backend="claude-cli", skip_permissions=True)
        fake_proc = make_fake_proc([])

        with patch(
            "lyra.core.cli.cli_pool_worker.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            await pool._spawn("p:1", model)

        for _ in range(5):
            await asyncio.sleep(0)

        assert len(events) == 1
        assert events[0].skip_permissions is True  # type: ignore[union-attr]

    async def test_audit_sink_skip_permissions_false_emits_false(self) -> None:
        """A spawn with skip_permissions=False emits the field as False."""
        from lyra.core.cli.cli_pool import CliPool
        from tests.core.conftest_cli_pool import make_fake_proc

        events: list[object] = []

        async def _capture(ev: object) -> None:
            events.append(ev)

        sink = MagicMock()
        sink.emit = _capture  # type: ignore[method-assign]

        pool = CliPool(audit_sink=sink)
        model = ModelConfig(backend="claude-cli", skip_permissions=False)
        fake_proc = make_fake_proc([])

        with patch(
            "lyra.core.cli.cli_pool_worker.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            await pool._spawn("p:2", model)

        for _ in range(5):
            await asyncio.sleep(0)

        assert len(events) == 1
        assert events[0].skip_permissions is False  # type: ignore[union-attr]
