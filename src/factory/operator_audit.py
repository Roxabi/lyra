"""Fail-soft bridge from Python CLIs to deploy/lib/operator-log.sh (ADR-093)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def _operator_log_sh(repo_root: Path) -> Path | None:
    sh = repo_root / "deploy" / "lib" / "operator-log.sh"
    return sh if sh.is_file() else None


_BRIDGE_TIMEOUT_S = 10


def _run_bash(repo_root: Path, body: str) -> None:
    """Fire-and-forget shell bridge — must never raise.

    Callers (``op_log``/``rotation_log_append``) are fail-soft by design
    ("logging must not break deploy"); a hang or OS-level error here must
    degrade to a missing log line, never propagate into caller code. This
    matters doubly now that ``_mode_full_provision`` flushes buffered
    rotation-log entries in a loop after seed generation succeeds (#2246) —
    if this raised mid-flush, the partial write would look identical to a
    real failure and could wrongly trigger ``_mode_regenerate``'s seed
    rollback for already-good seeds.
    """
    sh = _operator_log_sh(repo_root)
    if sh is None:
        return
    try:
        subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(sh))} && {body}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_BRIDGE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def op_log(repo_root: Path, event: str, **fields: str) -> None:
    """Append one JSONL line to operator.log. Never pass secret material."""
    args = [shlex.quote(event)]
    args.extend(f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in fields.items())
    _run_bash(repo_root, f"op_log {' '.join(args)}")


def rotation_log_append(
    repo_root: Path,
    secret: str,
    reason: str,
    *,
    trigger: str = "manual",
) -> None:
    """Append human-readable rotation record + rotation_log event."""
    _run_bash(
        repo_root,
        " ".join(
            [
                "rotation_log_append",
                shlex.quote(secret),
                shlex.quote(reason),
                shlex.quote(trigger),
            ]
        ),
    )
