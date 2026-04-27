"""Three-guard tests for roxabi_nats.testing.image. Mirrors spec #764.

Moved from roxabi_contracts/tests/test_image_testing_doubles.py per ADR-059 V6.
"""

from __future__ import annotations

import shutil

import pytest

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
