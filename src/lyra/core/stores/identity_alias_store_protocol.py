"""Protocol interface for the identity alias store.

Application-layer code (e.g. command handlers) must depend on this protocol
rather than on the concrete ``IdentityAliasStore`` class from
``lyra.infrastructure.stores.identity_alias_store``, following the
dependency-inversion principle (ADR-059).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["IdentityAliasStoreProtocol"]


@runtime_checkable
class IdentityAliasStoreProtocol(Protocol):
    """Structural interface for the identity alias store used by command handlers."""

    def resolve_aliases(self, platform_id: str) -> frozenset[str]: ...

    async def link(self, primary_id: str, secondary_id: str) -> None: ...

    async def unlink(self, platform_id: str) -> bool: ...

    async def create_challenge(
        self,
        initiator_id: str,
        platform: str,
        ttl_seconds: int = 300,
    ) -> str: ...

    async def validate_challenge(self, code: str) -> tuple[bool, str, str]: ...
