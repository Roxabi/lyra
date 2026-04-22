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
