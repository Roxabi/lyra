from __future__ import annotations

import shutil
import subprocess
import sys
from abc import ABC, abstractmethod


class NkeyProvider(ABC):
    @abstractmethod
    def gen_seed(self, name: str) -> bytes: ...

    @abstractmethod
    def pubkey_from_seed(self, seed: bytes) -> str: ...


class FakeNkeyProvider(NkeyProvider):
    """Deterministic per name — for tests only."""

    def gen_seed(self, name: str) -> bytes:
        return name.encode()

    def pubkey_from_seed(self, seed: bytes) -> str:
        name = seed.decode()
        return f"UDET{name.upper().replace('-', '')}"


class SubprocessNkeyProvider(NkeyProvider):
    def gen_seed(self, name: str) -> bytes:
        result = subprocess.run(["nk", "-gen", "user"], capture_output=True, check=True)
        return result.stdout.strip()

    def pubkey_from_seed(self, seed: bytes) -> str:
        # nk ≥0.4.8 dropped stdin-as-"-"; /dev/stdin works on Linux/macOS.
        result = subprocess.run(
            ["nk", "-inkey", "/dev/stdin", "-pubout"],
            input=seed,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip().decode()


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
