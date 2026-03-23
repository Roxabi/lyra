"""Compatibility shim — PairingManager moved to lyra.core.stores.pairing (S1)."""
from .stores.pairing import *  # noqa: F401, F403
from .stores.pairing import (
    PairingConfig,
    PairingError,
    PairingManager,
    get_pairing_manager,
    set_pairing_manager,
)
from .stores.pairing_config import _sha256  # noqa: F401 — re-export for tests

__all__ = [
    "PairingConfig",
    "PairingError",
    "PairingManager",
    "_sha256",
    "get_pairing_manager",
    "set_pairing_manager",
]
