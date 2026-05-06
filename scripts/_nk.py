from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod

# NATS user public nkeys: 1-byte U prefix + 32-byte ed25519 pubkey + 2-byte CRC,
# base32-encoded without padding → exactly 56 uppercase characters.
_NKEY_PUBKEY_LEN = 56

# Base32 alphabet (RFC 4648, uppercase, no padding)
_BASE32_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


class NkeyProvider(ABC):
    @abstractmethod
    def gen_seed(self, name: str) -> bytes: ...

    @abstractmethod
    def pubkey_from_seed(self, seed: bytes) -> str: ...


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


class SubprocessNkeyProvider(NkeyProvider):
    def gen_seed(self, name: str) -> bytes:
        result = subprocess.run(["nk", "-gen", "user"], capture_output=True, check=True)
        return result.stdout.strip()

    def pubkey_from_seed(self, seed: bytes) -> str:
        # nk >=0.4.8 no longer accepts stdin ("-" or /dev/stdin); use a tmp file.
        # Strip trailing whitespace before writing — seed files may carry a trailing
        # newline, and nk is finicky about extra whitespace on some versions.
        fd, tmp_path = tempfile.mkstemp(suffix=".seed")
        try:
            os.write(fd, seed.strip() + b"\n")
            os.close(fd)
            result = subprocess.run(
                ["nk", "-inkey", tmp_path, "-pubout"],
                capture_output=True,
                check=True,
            )
        finally:
            os.unlink(tmp_path)
        pubkey = result.stdout.strip().decode()
        if len(pubkey) != _NKEY_PUBKEY_LEN:
            raise ValueError(
                f"nk returned a {len(pubkey)}-char pubkey; "
                f"expected {_NKEY_PUBKEY_LEN}. "
                f"Raw output: {result.stdout!r}"
            )
        return pubkey


def ensure_nk_or_exit() -> None:
    if shutil.which("nk") is None:
        print(
            "error: nk binary not found on $PATH.\n"
            "Install: apt install nats-tools\n"
            "Or download from: https://github.com/nats-io/nkeys/releases\n"
            "and place at /usr/local/bin/nk",
            file=sys.stderr,
        )
        sys.exit(1)
