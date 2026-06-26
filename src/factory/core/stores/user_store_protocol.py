"""Protocol for the canonical user identity store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from factory.core.auth.user_models import PlatformIdentity, User

__all__ = ["UserStoreProtocol"]


@runtime_checkable
class UserStoreProtocol(Protocol):
    """Structural interface for resolving and linking cross-platform identities."""

    def resolve_user_id(self, platform_key: str) -> str | None: ...

    def resolve_platform_keys(self, user_id: str) -> frozenset[str]: ...

    def resolve_aliases(self, platform_key: str) -> frozenset[str]: ...

    async def ensure_user(
        self,
        platform_key: str,
        *,
        display_name: str | None = None,
    ) -> str: ...

    async def link_platform_keys(self, primary_key: str, secondary_key: str) -> str: ...

    async def unlink_platform_key(self, platform_key: str) -> bool: ...

    async def get_user(self, user_id: str) -> User | None: ...

    async def list_platform_identities(self, user_id: str) -> tuple[PlatformIdentity, ...]: ...