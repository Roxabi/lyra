from __future__ import annotations

import shutil
import string

import pytest
from scripts._nk import _NKEY_PUBKEY_LEN, SubprocessNkeyProvider

from tests.fakes.nkey_provider import FakeNkeyProvider

NK_AVAILABLE = shutil.which("nk") is not None

# Valid base32 chars used in NATS nkeys (RFC 4648 uppercase, no padding)
_NKEY_CHARS = set(string.ascii_uppercase + "234567")


@pytest.mark.skipif(not NK_AVAILABLE, reason="nk binary not on PATH — CI installs it")
def test_subprocess_nkey_roundtrip() -> None:
    """One gen_seed → pubkey_from_seed roundtrip; pubkey starts with U (NATS user prefix)."""  # noqa: E501
    provider = SubprocessNkeyProvider()
    seed = provider.gen_seed("test-identity")
    pubkey = provider.pubkey_from_seed(seed)
    assert pubkey.startswith("U"), (
        f"Expected NATS user pubkey (starts with U), got: {pubkey!r}"
    )
    assert len(pubkey) == _NKEY_PUBKEY_LEN, (
        f"Expected exactly {_NKEY_PUBKEY_LEN} chars; got {len(pubkey)}: {pubkey!r}"
    )


@pytest.mark.skipif(not NK_AVAILABLE, reason="nk binary not on PATH")
def test_subprocess_seed_is_bytes() -> None:
    """gen_seed returns bytes (seed content)."""
    provider = SubprocessNkeyProvider()
    seed = provider.gen_seed("test-identity")
    assert isinstance(seed, bytes)
    assert len(seed) > 0


# ── Regression tests for #1089: FakeNkeyProvider pubkey length ───────────────


class TestFakeNkeyProvider:
    """Regression suite for #1089 — FakeNkeyProvider must produce valid-length nkeys.

    Before the fix, pubkey_from_seed returned f"UDET{seed_content.upper()}"
    which produced 62-char strings when called with real seed bytes, breaking
    NATS auth.conf validation on M1.
    """

    def test_fake_pubkey_is_56_chars_from_name_seed(self) -> None:
        """pubkey_from_seed on a name-derived seed must be exactly 56 chars."""
        provider = FakeNkeyProvider()
        seed = provider.gen_seed("hub")
        pubkey = provider.pubkey_from_seed(seed)
        assert len(pubkey) == _NKEY_PUBKEY_LEN, (
            f"Expected {_NKEY_PUBKEY_LEN} chars, got {len(pubkey)}: {pubkey!r}"
        )

    def test_fake_pubkey_is_56_chars_from_real_seed_bytes(self) -> None:
        """pubkey_from_seed on real NATS seed bytes must also be exactly 56 chars.

        Regression: before #1089 fix, real seed bytes were concatenated as
        f"UDET{seed_content}" → 62 chars, rejected by nats-server.
        """
        provider = FakeNkeyProvider()
        real_seed = b"SUAJCEG22JM2DPM5FAPZEFVQZO3TKZD6YVPMDGOQJ5QXS6TGB33GEPM6DU\n"
        pubkey = provider.pubkey_from_seed(real_seed)
        assert len(pubkey) == _NKEY_PUBKEY_LEN, (
            f"Expected {_NKEY_PUBKEY_LEN} chars for real seed, got {len(pubkey)}: "
            f"{pubkey!r}. Likely regression: UDET+seed concatenation."
        )

    def test_fake_pubkey_starts_with_u(self) -> None:
        """pubkey_from_seed must start with U (NATS user nkey prefix)."""
        provider = FakeNkeyProvider()
        for name in ("hub", "discord-adapter", "clipool-worker"):
            seed = provider.gen_seed(name)
            pubkey = provider.pubkey_from_seed(seed)
            assert pubkey.startswith("U"), (
                f"pubkey for {name!r} must start with U; got: {pubkey!r}"
            )

    def test_fake_pubkey_no_embedded_newlines(self) -> None:
        """pubkey_from_seed must not contain newlines or whitespace.

        Secondary regression from #1089: rendered nkey strings spanned newlines,
        with closing quote on its own line.
        """
        provider = FakeNkeyProvider()
        real_seed = b"SUAJCEG22JM2DPM5FAPZEFVQZO3TKZD6YVPMDGOQJ5QXS6TGB33GEPM6DU\n"
        pubkey = provider.pubkey_from_seed(real_seed)
        assert "\n" not in pubkey, f"pubkey must not contain newline: {pubkey!r}"
        assert "\r" not in pubkey, f"pubkey must not contain CR: {pubkey!r}"
        assert " " not in pubkey, f"pubkey must not contain space: {pubkey!r}"

    def test_fake_pubkey_uses_base32_alphabet(self) -> None:
        """pubkey_from_seed must use only uppercase base32 chars (A-Z2-7)."""
        provider = FakeNkeyProvider()
        for name in ("hub", "telegram-adapter", "voice-tts"):
            seed = provider.gen_seed(name)
            pubkey = provider.pubkey_from_seed(seed)
            bad_chars = set(pubkey) - _NKEY_CHARS
            assert not bad_chars, (
                f"pubkey for {name!r} contains non-base32 chars {bad_chars}: {pubkey!r}"
            )

    def test_fake_pubkey_is_deterministic(self) -> None:
        """pubkey_from_seed must return the same value for the same seed."""
        provider = FakeNkeyProvider()
        seed = provider.gen_seed("hub")
        assert provider.pubkey_from_seed(seed) == provider.pubkey_from_seed(seed)

    def test_fake_pubkeys_are_unique_per_identity(self) -> None:
        """Different identity names must produce different pubkeys."""
        provider = FakeNkeyProvider()
        names = ["hub", "telegram-adapter", "discord-adapter", "clipool-worker"]
        pubkeys = [provider.pubkey_from_seed(provider.gen_seed(n)) for n in names]
        assert len(pubkeys) == len(set(pubkeys)), (
            f"pubkeys must be unique per identity; got duplicates: {pubkeys}"
        )
