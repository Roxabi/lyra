"""Integration tests for hub_standalone bootstrap helpers.

Covers:
- NATS_URL guard (SystemExit when env var missing)
- Lockfile lifecycle (acquire / release / stale PID / live PID block)
- Health endpoint basic response (no NATS required)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import lyra.bootstrap.hub_standalone as _hub_standalone_mod
from lyra.bootstrap.hub_standalone import _acquire_lockfile, _release_lockfile


# ---------------------------------------------------------------------------
# test_nats_url_guard_missing
# ---------------------------------------------------------------------------


class TestNatsUrlGuard:
    async def test_nats_url_guard_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_bootstrap_hub_standalone exits with SystemExit when NATS_URL is not set."""
        # Arrange
        monkeypatch.delenv("NATS_URL", raising=False)
        raw_config = _test_config()

        # Act / Assert — must exit before touching NATS
        from lyra.bootstrap.hub_standalone import _bootstrap_hub_standalone

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
        # Arrange — redirect _LOCKFILE to a temp path
        lockfile = tmp_path / "hub.lock"
        monkeypatch.setattr(_hub_standalone_mod, "_LOCKFILE", lockfile)

        # Act — acquire
        _acquire_lockfile()

        # Assert — file exists with correct PID
        assert lockfile.exists(), "Lockfile should be created by _acquire_lockfile()"
        assert lockfile.read_text().strip() == str(os.getpid())

        # Act — release
        _release_lockfile()

        # Assert — file is gone
        assert not lockfile.exists(), "Lockfile should be removed by _release_lockfile()"

    def test_lockfile_blocks_second_instance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_acquire_lockfile exits when the lockfile holds a live PID."""
        # Arrange — write our own PID (we are alive)
        lockfile = tmp_path / "hub.lock"
        lockfile.write_text(str(os.getpid()))
        monkeypatch.setattr(_hub_standalone_mod, "_LOCKFILE", lockfile)

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
        lockfile = tmp_path / "hub.lock"
        dead_pid = 99999999
        lockfile.write_text(str(dead_pid))
        monkeypatch.setattr(_hub_standalone_mod, "_LOCKFILE", lockfile)

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

        from lyra.bootstrap.health import create_health_app
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

        from lyra.bootstrap.health import create_health_app
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
        assert "adapters" in body, f"Expected 'adapters' key in health detail; got {body}"
        assert isinstance(body["adapters"], int)

    async def test_health_detail_unauthorized_without_secret(self) -> None:
        """GET /health/detail returns 401 when Authorization header is absent."""
        # Arrange
        import httpx

        from lyra.bootstrap.health import create_health_app
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
# Helpers
# ---------------------------------------------------------------------------


def _test_config() -> dict:
    """Minimal config dict that mirrors the structure of config.toml."""
    return {
        "defaults": {"cwd": "/tmp"},
        "admin": {"user_ids": ["test_admin"]},
        "telegram": {"bots": [{"bot_id": "test_bot", "agent": "test_agent"}]},
        "discord": {"bots": []},
        "auth": {
            "telegram_bots": [{"bot_id": "test_bot", "owner_id": "test_admin"}]
        },
    }
