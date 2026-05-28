from __future__ import annotations

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
            with os.fdopen(fd, "wb") as f:
                f.write(seed.strip() + b"\n")
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
