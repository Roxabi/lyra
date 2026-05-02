from __future__ import annotations

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
        raise NotImplementedError("SubprocessNkeyProvider not yet implemented (V2)")

    def pubkey_from_seed(self, seed: bytes) -> str:
        raise NotImplementedError("SubprocessNkeyProvider not yet implemented (V2)")


def ensure_nk_or_exit() -> None:
    raise NotImplementedError("ensure_nk_or_exit not yet implemented (V2)")
