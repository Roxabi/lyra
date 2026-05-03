"""Validate supervisor/quadlet wiring for NATS nkey seeds — #1017 T10."""

from __future__ import annotations

import re
from pathlib import Path

from scripts._acl_models import LoadedMatrix


def _files_wired(name: str, candidates: list[Path]) -> bool:
    """Return True if any file contains NATS_NKEY_SEED_PATH referencing <name>.seed."""
    pattern = re.compile(rf"NATS_NKEY_SEED_PATH=[^\s]*{re.escape(name)}\.seed")
    for path in candidates:
        try:
            text = path.read_text()
        except OSError:
            continue
        if pattern.search(text):
            return True
    return False


def validate_supervisor(matrix: LoadedMatrix, repo_root: Path) -> list[str]:
    """Check every owner==lyra active identity has NATS_NKEY_SEED_PATH in deploy files.

    Globs:
      repo_root/deploy/quadlet/*.container

    Note: deploy/conf.d/lyra-*.conf was deleted in #1036 (supervisord retired).
    Only Quadlet container units are checked.

    Returns list of error strings; empty = all wired.
    """
    conf_dir = repo_root / "deploy" / "conf.d"
    quadlet_dir = repo_root / "deploy" / "quadlet"

    conf_files = list(conf_dir.glob("lyra-*.conf")) if conf_dir.exists() else []
    quadlet_files = (
        list(quadlet_dir.glob("*.container")) if quadlet_dir.exists() else []
    )
    candidates = conf_files + quadlet_files

    errors: list[str] = []
    for name, identity in matrix["identities"].items():
        if identity.get("owner") != "lyra":
            continue
        if identity.get("status") == "retired":
            continue
        if not _files_wired(name, candidates):
            errors.append(
                f"identity '{name}' has no NATS_NKEY_SEED_PATH wiring"
                " in deploy/conf.d/ or deploy/quadlet/"
            )
    return errors
