"""Canonical filesystem location for the Roxabi factory data dirs."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["factory_data_dir", "factory_discord_data_dir"]


def factory_data_dir() -> Path:
    """Resolve the Roxabi factory data dir.

    Override with ``$ROXABI_FACTORY_DIR``; defaults to ``~/.roxabi/factory``.
    """
    default = str(Path.home() / ".roxabi" / "factory")
    return Path(os.environ.get("ROXABI_FACTORY_DIR", default))


def factory_discord_data_dir() -> Path:
    """Resolve the Discord adapter private data dir.

    Override with ``$ROXABI_FACTORY_DISCORD_DIR``; defaults to
    ``~/.roxabi/factory-discord``.  This dir is on a separate private volume
    (``factory-discord-data.volume``) not shared with ``factory-hub``.
    """
    default = str(Path.home() / ".roxabi" / "factory-discord")
    return Path(os.environ.get("ROXABI_FACTORY_DISCORD_DIR", default))
