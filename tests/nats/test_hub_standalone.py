"""Integration tests for hub_standalone bootstrap helpers.

Covers:
- NATS_URL guard (SystemExit when env var missing)
- Lockfile lifecycle (acquire / release / stale PID / live PID block)
- Health endpoint basic response (no NATS required)
- inbox_prefix kwarg passed to nats_connect (ADR-051, #715)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nats.aio.client import Client as NATS

from lyra.bootstrap.infra.lockfile import acquire_lockfile as _acquire_lockfile
from lyra.bootstrap.infra.lockfile import release_lockfile as _release_lockfile
from tests.nats.conftest import requires_nats_server

# ---------------------------------------------------------------------------
# test_nats_url_guard_missing
# ---------------------------------------------------------------------------


class TestNatsUrlGuard:
    async def test_nats_url_guard_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_bootstrap_hub_standalone exits with SystemExit when NATS_URL is not set."""
        # Arrange
        monkeypatch.delenv("NATS_URL", raising=False)
        raw_config = _test_config()

        # Act / Assert — must exit before touching NATS
        from lyra.bootstrap.standalone.hub_standalone import _bootstrap_hub_standalone

        with pytest.raises(SystemExit) as exc_info:
            await _bootstrap_hub_standalone(raw_config)

        assert exc_info.value.code is not None
        assert "NATS_URL" in str(exc_info.value.code)


# ---------------------------------------------------------------------------
# test_lockfile_created_and_cleaned
# ---------------------------------------------------------------------------


class TestLockfileLifecycle:
    def test_lockfile_created_and_cleaned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_acquire_lockfile writes PID; _release_lockfile removes the file."""
        # Arrange — point LYRA_VAULT_DIR at tmp_path so _lockfile() resolves there
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        lockfile = tmp_path.resolve() / "hub.lock"

        # Act — acquire
        _acquire_lockfile()

        # Assert — file exists with correct PID
        assert lockfile.exists(), "Lockfile should be created by _acquire_lockfile()"
        assert lockfile.read_text().strip() == str(os.getpid())

        # Act — release
        _release_lockfile()

        # Assert — file is gone
        assert not lockfile.exists(), (
            "Lockfile should be removed by _release_lockfile()"
        )

    def test_lockfile_blocks_second_instance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_acquire_lockfile exits when the lockfile holds a live PID."""
        # Arrange — write our own PID (we are alive)
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        lockfile = tmp_path.resolve() / "hub.lock"
        lockfile.write_text(str(os.getpid()))

        # Act / Assert — should sys.exit because PID is alive
        with pytest.raises(SystemExit) as exc_info:
            _acquire_lockfile()

        assert exc_info.value.code is not None
        # Message should mention the PID and lockfile path
        message = str(exc_info.value.code)
        assert str(os.getpid()) in message or "already running" in message

    def test_lockfile_overwrites_stale_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_acquire_lockfile overwrites a lockfile holding a dead PID."""
        # Arrange — write an impossibly high PID (guaranteed dead on Linux)
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        lockfile = tmp_path.resolve() / "hub.lock"
        dead_pid = 99999999
        lockfile.write_text(str(dead_pid))

        # Act — should NOT raise; stale lockfile is safe to overwrite
        _acquire_lockfile()

        # Assert — lockfile now holds current PID
        assert lockfile.exists()
        assert lockfile.read_text().strip() == str(os.getpid())

        # Cleanup
        _release_lockfile()


# ---------------------------------------------------------------------------
# test_health_endpoint_includes_adapters
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_endpoint_ok(self) -> None:
        """GET /health returns 200 ok=True without auth."""
        # Arrange
        import httpx

        from lyra.bootstrap.infra.health import create_health_app
        from lyra.core.hub import Hub

        hub = Hub()
        app = create_health_app(hub)

        # Act
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True

    async def test_health_detail_includes_adapters_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health/detail returns a dict with 'adapters' key when auth passes."""
        # Arrange — write a known health secret to tmp_path
        import httpx

        from lyra.bootstrap.infra.health import create_health_app
        from lyra.core.hub import Hub

        secret = "test-secret-abc"
        secret_dir = tmp_path / ".lyra" / "secrets"
        secret_dir.mkdir(parents=True)
        (secret_dir / "health_secret").write_text(secret)

        # Patch Path.home() so _read_secret picks up our temp secret
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        hub = Hub()
        app = create_health_app(hub)

        # Act
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/health/detail", headers={"Authorization": f"Bearer {secret}"}
            )

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert "adapters" in body, (
            f"Expected 'adapters' key in health detail; got {body}"
        )
        assert isinstance(body["adapters"], int)

    async def test_health_detail_unauthorized_without_secret(self) -> None:
        """GET /health/detail returns 401 when Authorization header is absent."""
        # Arrange
        import httpx

        from lyra.bootstrap.infra.health import create_health_app
        from lyra.core.hub import Hub

        hub = Hub()
        app = create_health_app(hub)

        # Act
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/detail")

        # Assert
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestStandaloneHubPipeline — SC-17: full pipeline integration
# ---------------------------------------------------------------------------


@requires_nats_server
class TestStandaloneHubPipeline:
    """SC-17: publish InboundMessage to NATS → Hub processes → staging queue drained.

    Wires NatsBus directly into Hub without the full bootstrap, bypassing the
    config/store/agent complexity while still exercising the real NATS transport
    and Hub.run() consumer loop.
    """

    async def test_nats_inbound_delivered_to_hub_run(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """Hub.run() consumes a message published to NATS inbound subject."""
        import asyncio

        import nats as nats_lib
        from lyra.core.auth.trust import TrustLevel
        from lyra.core.hub import Hub
        from lyra.core.messaging.message import InboundMessage, Platform
        from lyra.nats.nats_bus import NatsBus
        from roxabi_nats._serialize import serialize

        # Arrange — separate NATS connection for the Hub (mirrors production)
        hub_nc = await nats_lib.connect(nats_server_url)

        bot_id = "test_bot"
        platform = Platform.TELEGRAM
        inbound_subject = f"lyra.inbound.{platform.value}.{bot_id}"

        inbound_bus: NatsBus[InboundMessage] = NatsBus(
            nc=hub_nc, bot_id=bot_id, item_type=InboundMessage
        )
        inbound_bus.register(platform, bot_id=bot_id)
        await inbound_bus.start()

        hub = Hub(inbound_bus=inbound_bus)

        test_msg = InboundMessage(
            id="sc17-msg-001",
            platform=platform.value,
            bot_id=bot_id,
            scope_id="chat:999",
            user_id="user:sc17",
            user_name="SC17User",
            is_mention=False,
            text="integration test",
            text_raw="integration test",
            trust_level=TrustLevel.PUBLIC,
        )

        hub_task: asyncio.Task | None = None
        try:
            # Act — start Hub consumer loop, then publish via external NATS client
            hub_task = asyncio.create_task(hub.run(), name="hub-run")

            # Give Hub.run() a moment to enter its get() await
            await asyncio.sleep(0.05)

            payload = serialize(test_msg)
            await nc.publish(inbound_subject, payload)
            await nc.flush()

            # Wait up to 2 s for the staging queue to be drained by Hub.run()
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                if inbound_bus.staging_qsize() == 0:
                    break
                await asyncio.sleep(0.05)

            # Assert — staging queue is empty: Hub.run() consumed the message
            assert inbound_bus.staging_qsize() == 0, (
                "NatsBus staging queue not drained — Hub.run() did not consume"
                " the inbound message published to NATS"
            )
        finally:
            if hub_task is not None:
                hub_task.cancel()
                try:
                    await hub_task
                except asyncio.CancelledError:
                    pass
            await inbound_bus.stop()
            if hub_nc.is_connected:
                await hub_nc.drain()

    async def test_trust_re_resolution_invoked(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """ResolveTrustMiddleware calls Authenticator.resolve() for every inbound msg.

        Publishes an InboundMessage via NATS and verifies that the registered
        Authenticator's resolve() method is called — confirming the C3 trust
        re-resolution path runs in the standalone Hub pipeline.
        """
        import asyncio
        from unittest.mock import MagicMock

        import nats as nats_lib
        from lyra.core.auth.authenticator import Authenticator
        from lyra.core.auth.identity import Identity
        from lyra.core.auth.trust import TrustLevel
        from lyra.core.hub import Hub
        from lyra.core.messaging.message import InboundMessage, Platform
        from lyra.nats.nats_bus import NatsBus
        from roxabi_nats._serialize import serialize

        # Arrange — Hub with NatsBus and a mock Authenticator
        hub_nc = await nats_lib.connect(nats_server_url)

        bot_id = "trust_bot"
        platform = Platform.TELEGRAM
        inbound_subject = f"lyra.inbound.{platform.value}.{bot_id}"

        inbound_bus: NatsBus[InboundMessage] = NatsBus(
            nc=hub_nc, bot_id=bot_id, item_type=InboundMessage
        )
        inbound_bus.register(platform, bot_id=bot_id)
        await inbound_bus.start()

        hub = Hub(inbound_bus=inbound_bus)

        # Mock authenticator: returns PUBLIC identity for any user
        mock_auth = MagicMock(spec=Authenticator)
        mock_auth.resolve.return_value = Identity(
            user_id="user:trust", trust_level=TrustLevel.PUBLIC, is_admin=False
        )
        hub.register_authenticator(platform, bot_id, mock_auth)

        test_msg = InboundMessage(
            id="trust-msg-001",
            platform=platform.value,
            bot_id=bot_id,
            scope_id="chat:trust",
            user_id="user:trust",
            user_name="TrustUser",
            is_mention=False,
            text="trust test",
            text_raw="trust test",
            trust_level=TrustLevel.PUBLIC,
        )

        hub_task: asyncio.Task | None = None
        try:
            # Act — start Hub, publish message via NATS
            hub_task = asyncio.create_task(hub.run(), name="hub-run-trust")

            await asyncio.sleep(0.05)

            payload = serialize(test_msg)
            await nc.publish(inbound_subject, payload)
            await nc.flush()

            # Wait for the message to be consumed from the staging queue
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                if inbound_bus.staging_qsize() == 0:
                    break
                await asyncio.sleep(0.05)

            # Give Hub.run() a moment to finish pipeline processing after get()
            await asyncio.sleep(0.1)

            # Assert — Authenticator.resolve() was called with user_id from the message
            mock_auth.resolve.assert_called_once()
            call_args = mock_auth.resolve.call_args
            assert (
                call_args.args[0] == "user:trust"
                or call_args.kwargs.get("user_id") == "user:trust"
            )
        finally:
            if hub_task is not None:
                hub_task.cancel()
                try:
                    await hub_task
                except asyncio.CancelledError:
                    pass
            await inbound_bus.stop()
            if hub_nc.is_connected:
                await hub_nc.drain()


# ---------------------------------------------------------------------------
# T4 — hub_standalone passes inbox_prefix to nats_connect (ADR-051, #715)
# ---------------------------------------------------------------------------


class TestHubStandaloneInboxPrefix:
    async def test_bootstrap_hub_standalone_passes_inbox_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bootstrap calls nats_connect with inbox_prefix='_INBOX.hub' (ADR-051)."""
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        raw_config = _test_config()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nats_connect = AsyncMock(return_value=mock_nc)

        # Fake open_stores context manager — raises immediately so the test
        # doesn't need to wire the full bootstrap pipeline.
        @asynccontextmanager
        async def _fake_open_stores(*_args, **_kwargs):
            raise SystemExit("test-sentinel: stop after connect")
            yield  # pragma: no cover — required for asynccontextmanager shape

        with (
            patch(
                "lyra.bootstrap.standalone.hub_standalone.nats_connect",
                mock_nats_connect,
            ),
            patch(
                "lyra.bootstrap.standalone.hub_standalone.acquire_lockfile",
            ),
            patch(
                "lyra.bootstrap.standalone.hub_standalone.release_lockfile",
            ),
            patch(
                "lyra.bootstrap.standalone.hub_standalone.open_stores",
                _fake_open_stores,
            ),
        ):
            from lyra.bootstrap.standalone.hub_standalone import (
                _bootstrap_hub_standalone,
            )

            # Act — exits at open_stores; that's fine, we only need the connect call
            with pytest.raises(SystemExit, match="test-sentinel"):
                await _bootstrap_hub_standalone(raw_config)

        # Assert — nats_connect called once with the hub inbox prefix
        mock_nats_connect.assert_awaited_once()
        call_kwargs = mock_nats_connect.call_args.kwargs
        assert call_kwargs.get("inbox_prefix") == "_INBOX.hub", (
            f"Expected inbox_prefix='_INBOX.hub', got {call_kwargs!r}"
        )


# ---------------------------------------------------------------------------
# T10b — adapter_standalone passes platform inbox_prefix (ADR-051, #715)
# ---------------------------------------------------------------------------


class TestAdapterStandaloneInboxPrefix:
    @pytest.mark.parametrize(
        ("platform", "expected_prefix"),
        [
            ("telegram", "_INBOX.telegram-adapter"),
            ("discord", "_INBOX.discord-adapter"),
        ],
    )
    async def test_bootstrap_adapter_standalone_passes_platform_inbox_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        expected_prefix: str,
    ) -> None:
        """Adapter bootstrap passes per-platform inbox_prefix (ADR-051)."""
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        raw_config = _test_config()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.close = AsyncMock()

        # Raise immediately after connect so we don't need to stub deep platform logic.
        # Platform validation now runs before nats_connect (fail-fast), so the sentinel
        # is on nats_connect itself rather than Platform.
        mock_nats_connect = AsyncMock(
            side_effect=SystemExit("test-sentinel: stop after connect")
        )

        mock_platform_enum = MagicMock(return_value=MagicMock())

        with (
            patch(
                "lyra.bootstrap.standalone.adapter_standalone.nats_connect",
                mock_nats_connect,
            ),
            patch(
                "lyra.bootstrap.standalone.adapter_standalone.Platform",
                mock_platform_enum,
            ),
        ):
            from lyra.bootstrap.standalone.adapter_standalone import (
                _bootstrap_adapter_standalone,
            )

            # Act — exits at nats_connect after Platform validation; that's expected
            with pytest.raises(SystemExit, match="test-sentinel"):
                await _bootstrap_adapter_standalone(raw_config, platform)

        # Assert — nats_connect called with platform-derived inbox prefix
        mock_nats_connect.assert_awaited_once()
        call_kwargs = mock_nats_connect.call_args.kwargs
        assert call_kwargs.get("inbox_prefix") == expected_prefix, (
            f"platform={platform!r}: expected inbox_prefix={expected_prefix!r},"
            f" got {call_kwargs!r}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_config() -> dict:
    """Minimal config dict that mirrors the structure of config.toml."""
    return {
        "defaults": {"cwd": "/tmp"},
        "admin": {"user_ids": ["test_admin"]},
        "telegram": {"bots": [{"bot_id": "test_bot", "agent": "test_agent"}]},
        "discord": {"bots": []},
        "auth": {"telegram_bots": [{"bot_id": "test_bot", "owner_id": "test_admin"}]},
    }
