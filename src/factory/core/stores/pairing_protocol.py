"""Protocol interface for the Lyra pairing system.

Application-layer code (e.g. command handlers) must depend on this module
rather than on ``factory.infrastructure.stores.pairing`` directly, following
the dependency-inversion principle (ADR-059).

``PairingError`` is re-exported from ``pairing_config`` (pure core module).
``PairingManagerProtocol`` is the structural interface that command handlers
depend on. Injection happens via ``Pool.pairing_manager`` at bootstrap
(composition root) — no deferred import required (ADR-059 V4).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory.core.stores.pairing_config import PairingConfig, PairingError

__all__ = [
    "PairingError",
    "PairingManagerProtocol",
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
