"""Protocol interface for the Lyra pairing system.

Application-layer code (e.g. command handlers) must depend on this module
rather than on ``lyra.infrastructure.stores.pairing`` directly, following
the dependency-inversion principle (ADR-059).

``PairingError`` is re-exported from ``pairing_config`` (pure core module).
``PairingManagerProtocol`` is the structural interface that command handlers
depend on.
``get_pairing_manager`` is a thin facade that defers the import of the
infrastructure singleton so that ``commands/`` never has a direct
``lyra.infrastructure`` import.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lyra.core.stores.pairing_config import PairingConfig, PairingError

__all__ = [
    "PairingError",
    "PairingManagerProtocol",
    "get_pairing_manager",
]


@runtime_checkable
class PairingManagerProtocol(Protocol):
    """Structural interface for the pairing manager used by command handlers."""

    config: PairingConfig

    async def generate_code(self, admin_identity: str) -> str: ...

    async def validate_code(self, code: str, identity_key: str) -> tuple[bool, str]: ...

    async def revoke_session(self, identity_key: str) -> bool: ...

    def check_rate_limit(self, identity_key: str) -> bool: ...

    def record_failed_attempt(self, identity_key: str) -> None: ...


def get_pairing_manager() -> PairingManagerProtocol | None:
    """Return the module-level PairingManager from infrastructure.

    The import is deferred so that ``lyra.commands`` never takes a
    compile-time dependency on ``lyra.infrastructure``.
    """
    from lyra.infrastructure.stores.pairing import (  # noqa: PLC0415
        get_pairing_manager as _get,
    )

    return _get()
