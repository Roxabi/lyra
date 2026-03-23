"""Compatibility shim — PrefsStore moved to lyra.core.stores.prefs_store (S1)."""
from .stores.prefs_store import *  # noqa: F401, F403
from .stores.prefs_store import PrefsStore, UserPrefs

__all__ = ["PrefsStore", "UserPrefs"]
