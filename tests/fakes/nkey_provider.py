"""Deterministic fake nkey provider for tests."""

from __future__ import annotations

import hashlib

from scripts._nk import _BASE32_CHARS, _NKEY_PUBKEY_LEN, NkeyProvider


class FakeNkeyProvider(NkeyProvider):
    """Deterministic fake nkeys — for tests only.

    Produces structurally valid 56-char nkeys (U-prefix, base32 alphabet) derived
    deterministically from the seed bytes via SHA-256.  The keys are NOT
    cryptographically valid ed25519 keys; they are only suitable for unit tests
    that verify config rendering, not NATS auth.
    """

    def gen_seed(self, name: str) -> bytes:
        return name.encode()

    def pubkey_from_seed(self, seed: bytes) -> str:
        # Derive 55 deterministic base32 chars from SHA-256 of the seed, then
        # prepend 'U' (NATS user prefix) → exactly 56 chars, no embedded newlines.
        digest = hashlib.sha256(seed.strip()).digest()  # 32 bytes
        # Map each nibble (4 bits) of the digest to a base32 char.
        # 32 bytes = 256 bits; we need 55 chars × 5 bits = 275 bits — repeat digest.
        extended = (digest * 9)[:35]  # 35 bytes = 280 bits ≥ 275 bits needed
        chars = []
        bit_buf, bit_count = 0, 0
        for byte in extended:
            bit_buf = (bit_buf << 8) | byte
            bit_count += 8
            while bit_count >= 5 and len(chars) < _NKEY_PUBKEY_LEN - 1:
                bit_count -= 5
                chars.append(_BASE32_CHARS[(bit_buf >> bit_count) & 0x1F])
        return "U" + "".join(chars[: _NKEY_PUBKEY_LEN - 1])


__all__ = ["FakeNkeyProvider"]
