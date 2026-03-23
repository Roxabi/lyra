"""Compatibility shim — MessageIndex moved to lyra.core.stores.message_index (S1)."""
from .stores.message_index import *  # noqa: F401, F403
from .stores.message_index import MessageIndex

__all__ = ["MessageIndex"]
