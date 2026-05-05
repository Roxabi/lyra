"""Session management slash commands — ``/session list`` and ``/session resume <n>``.

The list is fetched fresh on every invocation (stateless): ``/session resume``
re-reads the index → ``cli_session_id`` mapping at execution time, avoiding a
stale-cache UX trap when the user lists, walks away, and resumes later.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..messaging.message import InboundMessage, Response
from ..pool import Pool
from .builtin_commands import require_admin

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
_TITLE_MAX = 60


def _format_age(iso_ts: str | None) -> str:
    if not iso_ts:
        return "?"
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    seconds = int((datetime.now(UTC) - ts).total_seconds())
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _truncate(text: str | None, n: int = _TITLE_MAX) -> str:
    if not text or not text.strip():
        return "(empty)"
    first_line = text.strip().splitlines()[0]
    if len(first_line) <= n:
        return first_line
    return first_line[: n - 1] + "…"


def _format_list(rows: list[dict], current_cli: str | None) -> str:
    if not rows:
        return "No past sessions for this chat."
    lines = ["Recent sessions:"]
    for i, row in enumerate(rows, 1):
        is_active = current_cli and row.get("cli_session_id") == current_cli
        marker = "►" if is_active else " "
        title = _truncate(row.get("first_user_msg"))
        age = _format_age(row.get("last_active_at"))
        turns = row.get("turn_count") or 0
        lines.append(f"  {marker} {i}. {title}  ·  {turns} turns  ·  {age}")
    lines.append("")
    lines.append("Use /session resume <n> to switch.")
    return "\n".join(lines)


async def _cmd_list(msg: InboundMessage, pool: Pool) -> Response:
    if denied := require_admin(msg):
        return denied
    store = pool.turn_store
    if store is None:
        return Response(content="Session history not available (no TurnStore).")
    rows = await store.list_sessions(pool.pool_id, _DEFAULT_LIMIT)
    current = await store.get_cli_session(pool.session_id)
    return Response(content=_format_list(rows, current))


async def _cmd_resume(msg: InboundMessage, args: list[str], pool: Pool) -> Response:
    if denied := require_admin(msg):
        return denied
    if len(args) < 2:
        return Response(content="Usage: /session resume <n>")
    try:
        idx = int(args[1])
    except ValueError:
        return Response(content="Not a number. Usage: /session resume <n>")
    if not pool.is_idle:
        return Response(
            content="A turn is in flight — wait for it to finish, or /stop first."
        )
    store = pool.turn_store
    if store is None:
        return Response(content="Session history not available (no TurnStore).")
    rows = await store.list_sessions(pool.pool_id, _DEFAULT_LIMIT)
    if not rows or idx < 1 or idx > len(rows):
        return Response(content=f"Index {idx} out of range. Run /session list first.")
    target = rows[idx - 1]
    cli_sid = target.get("cli_session_id")
    if not isinstance(cli_sid, str) or not cli_sid:
        return Response(
            content=f"Session #{idx} has no CLI session ID — cannot resume."
        )
    accepted = await pool.resume_session(cli_sid)
    if not accepted:
        return Response(content=f"Resume of session #{idx} was refused by the backend.")
    title = _truncate(target.get("first_user_msg"), 40)
    return Response(content=f"Resumed #{idx}: {title}")


async def cmd_session(
    msg: InboundMessage,
    args: list[str],
    pool: Pool | None,
) -> Response:
    """Dispatch ``/session`` subcommands."""
    if pool is None:
        return Response(content="No active pool.")
    sub = args[0].lower() if args else "list"
    if sub in ("", "list", "ls"):
        return await _cmd_list(msg, pool)
    if sub == "resume":
        return await _cmd_resume(msg, args, pool)
    return Response(content=f"Unknown subcommand: {sub!r}. Try: list | resume <n>")
