from __future__ import annotations

import shutil

import pytest

from scripts._nk import SubprocessNkeyProvider

NK_AVAILABLE = shutil.which("nk") is not None


@pytest.mark.skipif(not NK_AVAILABLE, reason="nk binary not on PATH — CI installs it")
def test_subprocess_nkey_roundtrip() -> None:
    """One gen_seed → pubkey_from_seed roundtrip; pubkey starts with U (NATS user prefix)."""
    provider = SubprocessNkeyProvider()
    seed = provider.gen_seed("test-identity")
    pubkey = provider.pubkey_from_seed(seed)
    assert pubkey.startswith("U"), f"Expected NATS user pubkey (starts with U), got: {pubkey!r}"
    assert len(pubkey) > 10


@pytest.mark.skipif(not NK_AVAILABLE, reason="nk binary not on PATH")
def test_subprocess_seed_is_bytes() -> None:
    """gen_seed returns bytes (seed content)."""
    provider = SubprocessNkeyProvider()
    seed = provider.gen_seed("test-identity")
    assert isinstance(seed, bytes)
    assert len(seed) > 0
