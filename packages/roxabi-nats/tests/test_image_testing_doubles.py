"""Three-guard tests for roxabi_nats.testing.image. Mirrors spec #764.

Moved from roxabi_contracts/tests/test_image_testing_doubles.py per ADR-059 V6.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from nats.errors import NoServersError

import nats as _nats
from roxabi_contracts.image.fixtures import tiny_png_1x1, tiny_png_mime
from roxabi_contracts.image.models import ImageRequest, ImageResponse
from roxabi_contracts.image.subjects import SUBJECTS
from roxabi_nats.testing.image import FakeImageWorker

requires_nats_server = pytest.mark.skipif(
    shutil.which("nats-server") is None,
    reason="nats-server not found in PATH — install via 'make nats-install'",
)


@pytest.fixture(autouse=True)
def _clear_lyra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LYRA_ENV", raising=False)


def test_g2_prod_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LYRA_ENV", "production")
    with pytest.raises(RuntimeError, match="FakeImageWorker cannot run in production"):
        FakeImageWorker()


def test_g2_prod_env_raises_even_when_g3_would_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LYRA_ENV", "production")
    with pytest.raises(RuntimeError):
        FakeImageWorker(nats_url="nats://127.0.0.1:4222")


@pytest.mark.parametrize(
    "bad_url",
    [
        "nats://10.0.0.5:4222",
        "nats://0.0.0.0:4222",
        "nats://localhost.evil.com:4222",
        "nats://example.com:4222",
    ],
)
async def test_g3_non_loopback_raises(bad_url: str) -> None:
    w = FakeImageWorker(nats_url=bad_url)
    with pytest.raises(ValueError, match="loopback"):
        await w.start()


async def test_g3_non_loopback_raises_when_g2_unset() -> None:
    """Guard 3 fires even with LYRA_ENV unset — proves G3 independent of G2."""
    w = FakeImageWorker(nats_url="nats://10.0.0.5:4222")
    with pytest.raises(ValueError, match="loopback"):
        await w.start()


@pytest.mark.parametrize(
    "prod_value",
    ["production", "PRODUCTION", "Production", "pRoDuCtIoN"],
)
def test_g2_prod_env_case_insensitive(
    prod_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard 2 uses casefold — every case variant of 'production' must fire."""
    monkeypatch.setenv("LYRA_ENV", prod_value)
    with pytest.raises(RuntimeError, match="cannot run in production"):
        FakeImageWorker()


async def test_start_twice_raises() -> None:
    """start() is not idempotent — a double-start is a programmer error."""
    w = FakeImageWorker(nats_url="nats://127.0.0.1:4222")
    w._nc = object()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="already started"):
        await w.start()


async def test_stop_is_idempotent_when_never_started() -> None:
    """stop() on a fresh worker is a no-op — never raises."""
    w = FakeImageWorker(nats_url="nats://127.0.0.1:4222")
    await w.stop()
    await w.stop()


async def test_stop_nulls_disconnected_nc() -> None:
    """stop() must null _nc/_sub even when _nc.is_connected is False."""
    from unittest.mock import MagicMock

    worker = FakeImageWorker()
    mock_nc = MagicMock()
    mock_nc.is_connected = False
    worker._nc = mock_nc  # type: ignore[assignment]
    worker._sub = object()  # type: ignore[assignment]
    await worker.stop()
    assert worker._nc is None
    assert worker._sub is None
    mock_nc.drain.assert_not_called()


# ---------------------------------------------------------------------------
# Guard 1 subprocess test
# ---------------------------------------------------------------------------


def test_g1_import_without_extra(tmp_path: Path) -> None:
    """Guard 1 — `import nats` at the top of testing.image fires when nats-py is absent.

    Stubs are injected for all roxabi_nats submodules that import nats at their own
    module top (adapter_base, connect, driver_base, _serialize), so the sabotaged
    nats.py is only hit by the Guard 1 tripwire line in roxabi_nats.testing.image.
    """
    sabotage = tmp_path / "nats.py"
    sabotage.write_text(
        textwrap.dedent(
            """
            raise ModuleNotFoundError("No module named 'nats' (sabotaged)", name="nats")
            """
        ).lstrip()
    )
    script = textwrap.dedent(
        """
        import sys, types

        def _stub(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            sys.modules[name] = m

        # Stub submodules that import nats at their module top so only
        # the Guard 1 tripwire in testing/image.py consumes the sabotage.
        _stub("roxabi_nats.adapter_base", NatsAdapterBase=None)
        _stub("roxabi_nats.connect", nats_connect=None)
        _stub("roxabi_nats.driver_base", NatsDriverBase=None)
        _stub("roxabi_nats._serialize", _TypeHintResolver=object)
        _stub(
            "roxabi_nats.testing._guards",
            assert_not_production=lambda cls_name: None,
            assert_loopback_url=lambda url: None,
        )

        try:
            import roxabi_nats.testing.image  # noqa: F401
        except ModuleNotFoundError as exc:
            if exc.name == "nats":
                sys.exit(42)
            raise
        sys.exit(0)
        """
    ).lstrip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": f"{tmp_path}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42, (
        f"expected exit 42 (ModuleNotFoundError for nats), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Guard 3 loopback-accept tests
# ---------------------------------------------------------------------------


async def _run_loopback_passes_guard(url: str) -> None:
    # Guard 3 must NOT raise ValueError. Connection error (no server) is expected.
    w = FakeImageWorker(nats_url=url)
    with pytest.raises((OSError, asyncio.TimeoutError, NoServersError)):
        await w.start()


async def test_g3_accepts_ipv4_loopback() -> None:
    await _run_loopback_passes_guard("nats://127.0.0.1:4222")


async def test_g3_accepts_ipv6_loopback() -> None:
    await _run_loopback_passes_guard("nats://[::1]:4222")


async def test_g3_accepts_ipv6_loopback_full() -> None:
    await _run_loopback_passes_guard("nats://[0:0:0:0:0:0:0:1]:4222")


# ---------------------------------------------------------------------------
# Roundtrip + behavioral tests
# ---------------------------------------------------------------------------

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "test-trace",
    "issued_at": datetime(2026, 4, 18, tzinfo=timezone.utc),
}


@requires_nats_server
async def test_image_roundtrip_default_fixture(nats_server_url: str) -> None:
    worker = FakeImageWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        req = ImageRequest(
            **_ENVELOPE,
            request_id="r1",
            prompt="a cat",
            engine="flux",
        )
        msg = await nc.request(
            SUBJECTS.image_request, req.model_dump_json().encode(), timeout=2.0
        )
        reply = ImageResponse.model_validate_json(msg.data)
        assert reply.ok is True
        assert reply.request_id == "r1"
        assert reply.mime_type == tiny_png_mime
        assert base64.b64decode(reply.image_b64) == tiny_png_1x1
        assert len(worker.calls) == 1
        assert worker.calls[0].prompt == "a cat"
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_image_roundtrip_seed_used_sentinel(nats_server_url: str) -> None:
    """seed_used=-1 when request has no seed (auto/random)."""
    worker = FakeImageWorker(nats_url=nats_server_url)
    await worker.start()
    try:
        nc = await _nats.connect(nats_server_url)
        req = ImageRequest(
            **_ENVELOPE, request_id="r-seed", prompt="dog", engine="flux"
        )
        msg = await nc.request(
            SUBJECTS.image_request, req.model_dump_json().encode(), timeout=2.0
        )
        reply = ImageResponse.model_validate_json(msg.data)
        assert reply.seed_used == -1
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_image_calls_records_multiple_requests_in_order(
    nats_server_url: str,
) -> None:
    worker = FakeImageWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        for i in range(3):
            req = ImageRequest(
                **_ENVELOPE, request_id=f"r{i}", prompt=f"p{i}", engine="flux"
            )
            await nc.request(
                SUBJECTS.image_request, req.model_dump_json().encode(), timeout=2.0
            )
        assert [r.request_id for r in worker.calls] == ["r0", "r1", "r2"]
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_image_dispatch_drops_malformed_json(nats_server_url: str) -> None:
    """_dispatch silently drops requests that fail Pydantic validation."""
    worker = FakeImageWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        await nc.publish(SUBJECTS.image_request, b"not json")
        await asyncio.sleep(0.1)
        assert worker.calls == []
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


@requires_nats_server
async def test_image_dispatch_no_reply_records_call(nats_server_url: str) -> None:
    """Fire-and-forget publish records the call but sends no reply."""
    worker = FakeImageWorker(nats_url=nats_server_url)
    await worker.start()
    assert worker.calls == []
    try:
        nc = await _nats.connect(nats_server_url)
        req = ImageRequest(
            **_ENVELOPE, request_id="r-fire", prompt="bird", engine="flux"
        )
        await nc.publish(SUBJECTS.image_request, req.model_dump_json().encode())
        await asyncio.sleep(0.1)
        assert len(worker.calls) == 1
        assert worker.calls[0].request_id == "r-fire"
        await nc.close()
    finally:
        await worker.stop()
        await asyncio.sleep(0.05)


def test_image_init_does_not_expose_testing() -> None:
    """Regression guard — roxabi_contracts.image.__init__ must NOT re-export testing."""
    import inspect

    import roxabi_contracts.image as image_mod

    assert "testing" not in image_mod.__all__
    src = inspect.getsource(image_mod)
    assert "testing" not in src
