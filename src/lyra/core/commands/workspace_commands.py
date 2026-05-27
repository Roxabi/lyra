"""Workspace / session command handlers extracted from CommandRouter (#298).

Async functions that manage cwd switching, workspace selection, and session clearing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..messaging.message import InboundMessage, Response
from ..pool import Pool
from .builtin_commands import require_admin


def _constrain_to_base(path: Path, base_dir: Path | None) -> Path | None:
    """Return *path* if it resolves inside *base_dir*, else None.

    Both paths are fully resolved (``..`` and symlinks expanded) before the
    containment check so that traversal tricks are neutralised.
    """
    if base_dir is None:
        return path
    resolved = path.resolve()
    base = base_dir.resolve()
    if not resolved.is_relative_to(base):
        return None
    return resolved


async def cmd_folder(
    msg: InboundMessage,
    args: list[str],
    pool: Pool | None,
    base_dir: Path | None = None,
) -> Response:
    """Switch working directory (admin-only)."""
    if denied := require_admin(msg):
        return denied
    if not args:
        return Response(content="Usage: /folder <path>")
    raw_path = Path(args[0]).expanduser().resolve()
    if not raw_path.is_dir():
        return Response(content=f"Not a directory: {args[0]}")
    constrained = _constrain_to_base(raw_path, base_dir)
    if constrained is None:
        return Response(content=f"Path escapes base directory: {args[0]}")
    if pool is None:
        return Response(content=f"cwd: {constrained}")
    await pool.switch_workspace(constrained)
    command_name = msg.text.split()[0]
    remaining = msg.text[len(command_name) + len(args[0]) + 1 :].lstrip()
    if remaining:
        followup = replace(msg, text=remaining, text_raw=remaining)
        pool.submit(followup)
    return Response(content=f"cwd → {constrained}")


async def cmd_workspace(
    msg: InboundMessage,
    args: list[str],
    pool: Pool | None,
    workspaces: dict[str, Path],
    base_dir: Path | None = None,
) -> Response:
    """List or switch workspaces (admin-only)."""
    if denied := require_admin(msg):
        return denied
    if not args or args[0] in ("ls", "list"):
        if not workspaces:
            return Response(content="No workspaces configured.")
        pairs = sorted(workspaces.items())
        rows = "\n".join(f"  {n} → {p}" for n, p in pairs)
        return Response(content=f"Workspaces:\n{rows}")
    ws_key = args[0]
    if ws_key not in workspaces:
        avail = ", ".join(sorted(workspaces)) or "none"
        return Response(content=f"Unknown workspace: {ws_key}. Available: {avail}")
    cwd = workspaces[ws_key]
    constrained = _constrain_to_base(cwd, base_dir)
    if constrained is None:
        return Response(
            content=f"Workspace path escapes base directory: {ws_key} → {cwd}"
        )
    if pool is None:
        return Response(content=f"Workspace: {ws_key}")
    await pool.switch_workspace(constrained)
    prefix = f"/workspace {ws_key}"
    remaining = msg.text[len(prefix) :].lstrip()
    if remaining:
        rr = msg.text_raw[len(prefix) :].lstrip() if msg.text_raw else remaining
        pool.submit(replace(msg, text=remaining, text_raw=rr))
    return Response(content=f"Workspace: {ws_key}")


async def cmd_clear(pool: Pool | None) -> Response:
    """Clear conversation history and reset session."""
    if pool is None:
        return Response(content="No active session to clear.")
    pool.history.clear()
    await pool.reset_session()
    return Response(content="Conversation history cleared. Starting fresh.")
