"""Canonical filesystem location for the Roxabi factory data dir."""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["factory_data_dir"]


def factory_data_dir() -> Path:
    """Resolve the Roxabi factory data dir.

    Override with ``$ROXABI_FACTORY_DIR``; defaults to ``~/.roxabi/factory``.
    """
    default = str(Path.home() / ".roxabi" / "factory")
    return Path(os.environ.get("ROXABI_FACTORY_DIR", default))
