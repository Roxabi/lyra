"""Per-turn tool activity accumulator + formatter for the recap card.

Rebuild of v1 `tool_recap_format.py` on top of v2 ToolCall{Start,Args,End}RenderEvent.
Pure module — no framework imports, no I/O. Adapter-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lyra.core.messaging.render_events import (
    FileEditSummary,
    SilentCounts,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)

_BASH_DISPLAY_MAX = 80
_AGENT_DISPLAY_MAX = 48
_BASH_GROUP_THRESHOLD = 3
_FILES_GROUP_THRESHOLD = 3
_NAMES_THRESHOLD = 5


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len chars, appending … when shortened."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _plural(n: int, word: str) -> str:
    """Return '3 reads' / '1 read'."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def _sanitize(text: str) -> str:
    """Strip backticks so text can be safely wrapped in inline code."""
    return text.replace("`", "")


def _accumulate_file_edit(
    accum: "ToolRecapAccumulator", call_id: str, tool_name: str, args: dict
) -> None:
    """Update the files dict for a single edit/write call."""
    path = args.get("path", call_id)
    existing = accum.files.get(path)
    if existing is None:
        new_count, new_edits = 1, [tool_name]
    else:
        new_count = existing.count + 1
        new_edits = list(existing.edits) + [tool_name]
    if new_count > _NAMES_THRESHOLD:
        new_edits = []
    accum.files[path] = FileEditSummary(path=path, edits=new_edits, count=new_count)


@dataclass
class _PartialCall:
    tool_name: str
    args_buffer: str = ""


@dataclass
class ToolRecapAccumulator:
    """Accumulates tool call events for a single turn."""

    files: dict[str, FileEditSummary] = field(default_factory=dict)
    bash_commands: list[str] = field(default_factory=list)
    web_fetches: list[str] = field(default_factory=list)
    web_searches: list[str] = field(default_factory=list)
    agent_calls: list[str] = field(default_factory=list)
    unknown_calls: dict[str, int] = field(default_factory=dict)
    _silent_reads: int = 0
    _silent_greps: int = 0
    _silent_globs: int = 0
    _in_flight: dict[str, _PartialCall] = field(default_factory=dict)

    def observe_start(self, ev: ToolCallStartRenderEvent) -> None:
        """Register a new in-flight tool call."""
        self._in_flight[ev.tool_call_id] = _PartialCall(tool_name=ev.tool_name)

    def observe_args(self, ev: ToolCallArgsRenderEvent) -> None:
        """Append an args delta to the in-flight call buffer."""
        partial = self._in_flight.get(ev.tool_call_id)
        if partial is not None:
            partial.args_buffer += ev.delta

    def _route(self, tool_call_id: str, key: str, tool_name: str, args: dict) -> None:
        """Route a completed tool call into the appropriate accumulator bucket."""
        if key in ("edit", "write"):
            _accumulate_file_edit(self, tool_call_id, tool_name, args)
        elif key == "bash":
            self.bash_commands.append(args.get("command", ""))
        elif key == "read":
            self._silent_reads += 1
        elif key == "grep":
            self._silent_greps += 1
        elif key == "glob":
            self._silent_globs += 1
        elif key in ("web_fetch", "webfetch"):
            self.web_fetches.append(args.get("url", ""))
        elif key in ("web_search", "websearch"):
            self.web_searches.append(args.get("query", ""))
        elif key == "agent":
            self.agent_calls.append(args.get("description", "agent"))
        else:
            self.unknown_calls[key] = self.unknown_calls.get(key, 0) + 1

    def observe_end(self, ev: ToolCallEndRenderEvent) -> None:
        """Finalise a tool call and route it into the appropriate bucket."""
        partial = self._in_flight.get(ev.tool_call_id)
        if partial is None:
            return
        try:
            args: dict = json.loads(partial.args_buffer)
        except (json.JSONDecodeError, ValueError):
            args = {}
        self._route(ev.tool_call_id, partial.tool_name.lower(), partial.tool_name, args)
        del self._in_flight[ev.tool_call_id]

    def observe_result(self, ev: ToolCallResultRenderEvent) -> None:
        """No-op — result events are not tracked in the recap card."""
        del ev

    def snapshot_silent(self) -> SilentCounts:
        """Return a frozen view of current silent counters."""
        return SilentCounts(
            reads=self._silent_reads,
            greps=self._silent_greps,
            globs=self._silent_globs,
        )

    def has_any(self) -> bool:
        """Return True if any tool activity has been accumulated."""
        return bool(
            self.files
            or self.bash_commands
            or self.web_fetches
            or self.web_searches
            or self.agent_calls
            or self.unknown_calls
            or self._silent_reads > 0
            or self._silent_greps > 0
            or self._silent_globs > 0
        )


def _format_files(accum: ToolRecapAccumulator) -> list[str]:
    """Build lines for the file-edit section."""
    if not accum.files:
        return []
    if len(accum.files) >= _FILES_GROUP_THRESHOLD:
        total = sum(f.count for f in accum.files.values())
        return [f"✏️ {len(accum.files)} files · {total} edits"]
    lines: list[str] = []
    for summary in accum.files.values():
        label = ", ".join(summary.edits) if summary.edits else f"×{summary.count}"
        path = _sanitize(summary.path)
        lines.append(f"✏️ `{path}` ({label})")
    return lines


def _format_bash(accum: ToolRecapAccumulator) -> list[str]:
    """Build lines for the bash-command section."""
    cmds = [c for c in (s.strip() for s in accum.bash_commands) if c]
    if not cmds:
        return []
    if len(cmds) >= _BASH_GROUP_THRESHOLD:
        return [f"\U0001f4bb {_plural(len(cmds), 'command')}"]
    return [f"\U0001f4bb `{_sanitize(_truncate(c, _BASH_DISPLAY_MAX))}`" for c in cmds]


def _format_unknown(accum: ToolRecapAccumulator) -> list[str]:
    """Build lines for unknown tools, sorted alphabetically."""
    return [
        f"\U0001f527 {count} {name}"
        for name, count in sorted(accum.unknown_calls.items())
        if count > 0
    ]


def _format_silent(accum: ToolRecapAccumulator) -> list[str]:
    """Build line for silent-count breakdown."""
    parts: list[str] = []
    if accum._silent_reads:
        parts.append(_plural(accum._silent_reads, "read"))
    if accum._silent_greps:
        parts.append(_plural(accum._silent_greps, "grep"))
    if accum._silent_globs:
        parts.append(_plural(accum._silent_globs, "glob"))
    if not parts:
        return []
    return [f"\U0001f50d {' · '.join(parts)}"]


def format_recap_lines(accum: ToolRecapAccumulator, *, done: bool) -> list[str]:
    """Build tool recap card lines in canonical order."""
    if not accum.has_any():
        return []
    header = "\U0001f527 Done ✅" if done else "\U0001f527 Working…"
    lines: list[str] = [header]
    lines.extend(_format_files(accum))
    lines.extend(_format_bash(accum))
    for url in accum.web_fetches:
        lines.append(f"\U0001f310 {url}")
    for query in accum.web_searches:
        lines.append(f"\U0001f310 {query}")
    for desc in accum.agent_calls:
        desc = desc.strip() or "agent"
        lines.append(f"\U0001f916 {_truncate(desc, _AGENT_DISPLAY_MAX)}")
    lines.extend(_format_unknown(accum))
    lines.extend(_format_silent(accum))
    return lines


__all__ = ["ToolRecapAccumulator", "format_recap_lines"]
