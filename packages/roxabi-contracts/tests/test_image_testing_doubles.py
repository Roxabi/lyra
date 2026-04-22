"""Three-guard tests for roxabi_contracts.image.testing. Mirrors spec #764."""

from __future__ import annotations

import pytest

# Imports here (not inside fixtures) to prove the module loads in the test
# env — Guard 1 (import-time) is exercised implicitly; if the [testing]
# extra is missing, `nats` is absent and this import fails.
from roxabi_contracts.image.testing import FakeImageWorker


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
    """start() is not idempotent — a double-start is a programmer error.

    Guard 3 fires first (loopback check) so we point at a non-loopback URL
    to exit before ``nats_connect``. Reaching the ``_nc is not None`` branch
    would require a real NATS server; the guard short-circuits earlier.

    Instead, we seed ``_nc`` manually to simulate "already started" state
    and verify the RuntimeError fires instead of a silent second connect.
    """
    w = FakeImageWorker(nats_url="nats://127.0.0.1:4222")
    w._nc = object()  # type: ignore[assignment]  # simulate started state
    with pytest.raises(RuntimeError, match="already started"):
        await w.start()


async def test_stop_is_idempotent_when_never_started() -> None:
    """stop() on a fresh worker is a no-op — never raises."""
    w = FakeImageWorker(nats_url="nats://127.0.0.1:4222")
    await w.stop()  # no start(), no exception
    await w.stop()  # second stop, still no exception
