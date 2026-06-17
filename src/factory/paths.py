"""Canonical filesystem location for the Roxabi factory data dirs."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["factory_data_dir", "factory_discord_data_dir", "factory_turns_db_path"]


def factory_data_dir() -> Path:
    """Resolve the Roxabi factory data dir.

    Override with ``$ROXABI_FACTORY_DIR``; defaults to ``~/.roxabi/factory``.
    """
    default = str(Path.home() / ".roxabi" / "factory")
    return Path(os.environ.get("ROXABI_FACTORY_DIR", default))


def factory_discord_data_dir() -> Path:
    """Resolve the Discord adapter private data dir.

    Override with ``$ROXABI_FACTORY_DISCORD_DIR``; defaults to
    ``~/.roxabi/factory/discord`` (a sub-path of ``factory_data_dir()``).
    In production the named volume ``factory-discord-data`` is mounted at
    the in-container path ``~/.roxabi/factory/discord``; the Discord adapter
    never mounts the hub's ``factory-data.volume`` parent.
    """
    env = os.environ.get("ROXABI_FACTORY_DISCORD_DIR")
    if env:
        return Path(env)
    return factory_data_dir() / "discord"


def factory_turns_db_path() -> Path:
    """Resolve the canonical turns.db path (hub read + turn-writer write).

    Override with ``$FACTORY_TURNS_DB``; defaults to
    ``<factory_data_dir>/turn-writer/turns.db`` so WAL siblings co-locate
    with the sole writer (``factory-turn-writer`` Quadlet unit).
    """
    env = os.environ.get("FACTORY_TURNS_DB")
    if env:
        return Path(env)
    return factory_data_dir() / "turn-writer" / "turns.db"
