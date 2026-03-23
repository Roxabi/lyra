"""Compatibility shim — AuthStore moved to lyra.core.stores.auth_store (S1)."""
from .stores.auth_store import *  # noqa: F401, F403
from .stores.auth_store import AuthStore

__all__ = ["AuthStore"]
