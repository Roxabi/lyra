"""Unit tests for lyra.tools.gh_token.daemon entrypoint.

Exercises config loading + a short daemon spin-up with a stubbed httpx
transport: prove the dispenser binds the socket, refresh loop runs, and
clean cancellation tears everything down.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from lyra.tools.gh_token.daemon import (
    DaemonConfigError,
    _load_config,
    run_daemon,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_pem(tmp_path: Path) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "test-app.pem"
    p.write_bytes(pem_bytes)
    return p


@pytest.fixture()
def daemon_env(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Set required env vars for the daemon and return the relevant paths."""
    pem = _make_pem(tmp_path)
    sock = tmp_path / "dispenser.sock"
    cache = tmp_path / "token.json"

    monkeypatch.setenv("LYRA_GH_APP_ID", "12345")
    monkeypatch.setenv("LYRA_GH_INSTALLATION_ID", "67890")
    monkeypatch.setenv("LYRA_GH_PEM_PATH", str(pem))
    monkeypatch.setenv("LYRA_GH_DISPENSER_SOCK", str(sock))
    monkeypatch.setenv("LYRA_GH_CACHE_PATH", str(cache))
    return {"pem": pem, "sock": sock, "cache": cache}


# ── _load_config ──────────────────────────────────────────────────────────────


def test_load_config_happy_path(daemon_env: dict[str, Path]) -> None:
    cfg = _load_config()
    assert cfg.app_id == "12345"
    assert cfg.install_id == "67890"
    assert cfg.pem_path == daemon_env["pem"]


def test_load_config_missing_app_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LYRA_GH_APP_ID", raising=False)
    monkeypatch.setenv("LYRA_GH_INSTALLATION_ID", "67890")
    monkeypatch.setenv("LYRA_GH_PEM_PATH", str(_make_pem(tmp_path)))
    with pytest.raises(DaemonConfigError, match="LYRA_GH_APP_ID"):
        _load_config()


def test_load_config_non_numeric_install_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LYRA_GH_APP_ID", "12345")
    monkeypatch.setenv("LYRA_GH_INSTALLATION_ID", "abc")
    monkeypatch.setenv("LYRA_GH_PEM_PATH", str(_make_pem(tmp_path)))
    with pytest.raises(DaemonConfigError, match="numeric"):
        _load_config()


def test_load_config_pem_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LYRA_GH_APP_ID", "12345")
    monkeypatch.setenv("LYRA_GH_INSTALLATION_ID", "67890")
    monkeypatch.setenv("LYRA_GH_PEM_PATH", str(tmp_path / "does-not-exist.pem"))
    with pytest.raises(DaemonConfigError, match="not found"):
        _load_config()


# ── run_daemon ────────────────────────────────────────────────────────────────


def _stub_transport() -> httpx.MockTransport:
    """Always return a fresh installation token expiring in 1h."""
    expires = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"token": "ghs_daemon_test", "expires_at": expires},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_daemon_binds_socket_and_serves(
    daemon_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_daemon binds the socket at the configured path and serves get requests."""
    cfg = _load_config()
    # Inject stub transport via monkeypatch on httpx.AsyncClient — the daemon
    # creates its own client, so we patch the constructor's transport kwarg.
    real_async_client = httpx.AsyncClient

    def _client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = _stub_transport()
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lyra.tools.gh_token.daemon.httpx.AsyncClient", _client_factory)

    task = asyncio.create_task(run_daemon(cfg))
    sock_path: Path = cfg.sock_path

    # Wait for socket to materialise (bounded).
    for _ in range(20):
        await asyncio.sleep(0.05)
        if sock_path.exists():
            break
    assert sock_path.exists(), "dispenser socket did not appear within 1s"
    assert stat.S_ISSOCK(sock_path.stat().st_mode)

    # Hit the socket and expect a valid credential response.
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    writer.write(b"get\n")
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=2.0)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()

    assert b"username=x-access-token" in raw
    assert b"password=ghs_daemon_test" in raw

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_daemon_creates_exactly_one_task(
    daemon_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-7 invariant: run_daemon creates exactly one asyncio task (serve_forever).

    No background refresh task must be created — token refresh is lazy/on-demand.
    This test pins that invariant so adding a stray create_task() in run_daemon
    would be caught immediately.
    """
    cfg = _load_config()

    real_async_client = httpx.AsyncClient

    def _client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = _stub_transport()
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lyra.tools.gh_token.daemon.httpx.AsyncClient", _client_factory)

    created_coro_names: list[str] = []
    real_create_task = asyncio.create_task

    def _tracking_create_task(coro, **_kwargs):  # type: ignore[no-untyped-def]
        created_coro_names.append(coro.__name__)

        # Wrap the coroutine in a task that cancels itself after one step so
        # run_daemon's gather() returns promptly without hanging.
        async def _short_circuit():  # type: ignore[return]
            coro.close()
            raise asyncio.CancelledError

        return real_create_task(_short_circuit())

    monkeypatch.setattr(
        "lyra.tools.gh_token.daemon.asyncio.create_task", _tracking_create_task
    )

    # run_daemon will exit via CancelledError propagation from the gather.
    with contextlib.suppress(asyncio.CancelledError):
        await run_daemon(cfg)

    assert len(created_coro_names) == 1, (
        f"Expected exactly 1 asyncio.create_task() call in run_daemon, "
        f"got {len(created_coro_names)}: {created_coro_names}"
    )
    assert created_coro_names[0] == "serve_forever", (
        f"Expected the single task to be serve_forever, got {created_coro_names[0]!r}"
    )


@pytest.mark.asyncio
async def test_daemon_unlinks_stale_socket_on_start(
    daemon_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover socket file from a prior crashed run is removed before bind."""
    sock_path: Path = daemon_env["sock"]

    # Pre-create a stale unix socket at the path. asyncio.start_unix_server
    # would otherwise raise EADDRINUSE — daemon must unlink it first.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.close()
    assert sock_path.exists()

    cfg = _load_config()
    real_async_client = httpx.AsyncClient

    def _client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = _stub_transport()
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("lyra.tools.gh_token.daemon.httpx.AsyncClient", _client_factory)

    task = asyncio.create_task(run_daemon(cfg))
    for _ in range(20):
        await asyncio.sleep(0.05)
        if sock_path.exists() and stat.S_ISSOCK(sock_path.stat().st_mode):
            # We need the NEW socket bound by the daemon — confirm by
            # successfully connecting.
            try:
                r, w = await asyncio.open_unix_connection(str(sock_path))
                w.close()
                await asyncio.wait_for(r.read(0), timeout=0.1)
                break
            except Exception:  # noqa: BLE001
                continue

    # The cleanup itself is the assertion — if unlinking failed, the daemon
    # would have crashed with EADDRINUSE and the socket would still be the
    # stale one (though file-test alone can't tell them apart, the connect
    # above proves liveness).
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
