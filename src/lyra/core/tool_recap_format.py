"""Shared tool-recap line builder for outbound adapters.

Both Telegram (``_format_tool_summary``) and Discord (``_build_tool_embed``)
render a ``ToolSummaryRenderEvent`` as a list of human-readable lines.  This
module centralises that logic so fixes and improvements land in one place.

No framework imports (aiogram, discord, anthropic) are permitted here.
"""

from __future__ import annotations

from .render_events import ToolSummaryRenderEvent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum visible length for a bash command line before truncation.
_BASH_DISPLAY_MAX = 80

#: Maximum visible length for an agent description before truncation.
_AGENT_DISPLAY_MAX = 48

#: Number of bash commands before collapsing to a single count line.
_BASH_GROUP_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_code(text: str) -> str:
    """Strip backticks so text can be safely wrapped in inline code."""
    return text.replace("`", "")


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* chars, appending ``\u2026`` when shortened."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _plural(n: int, word: str) -> str:
    """Return ``'3 reads'`` / ``'1 read'``."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def _format_files(event: ToolSummaryRenderEvent) -> list[str]:
    """Build lines for file-edit section."""
    if not event.files:
        return []
    if len(event.files) >= 3:
        total = sum(f.count for f in event.files.values())
        return [f"\u270f\ufe0f {len(event.files)} files \u00b7 {total} edits"]
    lines: list[str] = []
    for summary in event.files.values():
        if summary.edits:
            label = ", ".join(summary.edits)
        else:
            label = f"\u00d7{summary.count}"
        path = _sanitize_code(summary.path)
        lines.append(f"\u270f\ufe0f `{path}` ({label})")
    return lines


def _format_bash(event: ToolSummaryRenderEvent) -> list[str]:
    """Build lines for bash-command section."""
    cmds = [c for c in (s.strip() for s in event.bash_commands) if c]
    if not cmds:
        return []
    if len(cmds) >= _BASH_GROUP_THRESHOLD:
        return [f"\U0001f4bb {_plural(len(cmds), 'command')}"]
    return [
        f"\U0001f4bb `{_sanitize_code(_truncate(c, _BASH_DISPLAY_MAX))}`" for c in cmds
    ]


def _format_silent(event: ToolSummaryRenderEvent) -> list[str]:
    """Build line for silent-count breakdown."""
    sc = event.silent_counts
    parts: list[str] = []
    if sc.reads:
        parts.append(_plural(sc.reads, "read"))
    if sc.greps:
        parts.append(_plural(sc.greps, "grep"))
    if sc.globs:
        parts.append(_plural(sc.globs, "glob"))
    if not parts:
        return []
    return [f"\U0001f50d {' \u00b7 '.join(parts)}"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_tool_lines(event: ToolSummaryRenderEvent) -> list[str]:
    """Build the tool-recap body lines shared by all outbound adapters."""
    lines: list[str] = []
    lines.extend(_format_files(event))
    lines.extend(_format_bash(event))
    for url in event.web_fetches:
        lines.append(f"\U0001f310 {url}")
    for desc in event.agent_calls:
        desc = desc.strip() or "agent"
        lines.append(f"\U0001f916 {_truncate(desc, _AGENT_DISPLAY_MAX)}")
    lines.extend(_format_silent(event))
    return lines


__all__ = ["format_tool_lines"]
