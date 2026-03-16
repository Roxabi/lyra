"""Workspace / session command handlers extracted from CommandRouter (#298).

Async functions that manage cwd switching, workspace selection, and session clearing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .builtin_commands import require_admin
from .message import InboundMessage, Response
from .pool import Pool


async def cmd_folder(
    msg: InboundMessage,
    args: list[str],
    pool: Pool | None,
) -> Response:
    """Switch working directory (admin-only)."""
    if denied := require_admin(msg):
        return denied
    if not args:
        return Response(content="Usage: /folder <path>")
    raw_path = Path(args[0]).expanduser().resolve()
    if not raw_path.is_dir():
        return Response(content=f"Not a directory: {args[0]}")
    if pool is None:
        return Response(content=f"cwd: {raw_path}")
    await pool.switch_workspace(raw_path)
    command_name = msg.text.split()[0]
    remaining = msg.text[len(command_name) + len(args[0]) + 1 :].lstrip()
    if remaining:
        followup = replace(msg, text=remaining, text_raw=remaining)
        pool.submit(followup)
    return Response(content=f"cwd → {raw_path}")


async def cmd_workspace(
    msg: InboundMessage,
    args: list[str],
    pool: Pool | None,
    workspaces: dict[str, Path],
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
    if pool is None:
        return Response(content=f"Workspace: {ws_key}")
    await pool.switch_workspace(cwd)
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
    pool.sdk_history.clear()
    pool.history.clear()
    await pool.reset_session()
    return Response(content="Conversation history cleared. Starting fresh.")
