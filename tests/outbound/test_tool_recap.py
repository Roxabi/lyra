"""RED tests for ToolDisplayConfig wiring into ToolRecapAccumulator (#1336 Slice 1).

All tests import from lyra.outbound._tool_recap (future path after T2 file move)
and assert ToolRecapAccumulator(config=...) which lands in T5. Tests are
intentionally RED until Phase B Slices 1-2 are implemented.
"""

from __future__ import annotations

import json

import pytest

from lyra.core.messaging.render_events import (
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.tool_display_config import ToolDisplayConfig

# RED import: lyra.outbound._tool_recap does not exist yet (T2 will create it
# by moving adapters/shared/_tool_recap.py → outbound/_tool_recap.py)
from lyra.outbound._tool_recap import ToolRecapAccumulator, format_recap_lines

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed_bash(
    accum: ToolRecapAccumulator, call_id: str, command: str
) -> None:
    """Feed a single complete bash tool call through the accumulator."""
    accum.observe_start(
        ToolCallStartRenderEvent(tool_call_id=call_id, tool_name="bash")
    )
    accum.observe_args(
        ToolCallArgsRenderEvent(
            tool_call_id=call_id,
            delta=json.dumps({"command": command}),
        )
    )
    accum.observe_end(ToolCallEndRenderEvent(tool_call_id=call_id))


def _feed_tool(
    accum: ToolRecapAccumulator,
    call_id: str,
    tool_name: str,
    args_json: str,
) -> None:
    """Feed a single complete tool call with arbitrary args JSON."""
    accum.observe_start(
        ToolCallStartRenderEvent(tool_call_id=call_id, tool_name=tool_name)
    )
    accum.observe_args(
        ToolCallArgsRenderEvent(tool_call_id=call_id, delta=args_json)
    )
    accum.observe_end(ToolCallEndRenderEvent(tool_call_id=call_id))


def _feed_file(
    accum: ToolRecapAccumulator,
    call_id: str,
    path: str,
    tool_name: str = "edit",
) -> None:
    """Feed a single file edit/write call."""
    _feed_tool(accum, call_id, tool_name, json.dumps({"path": path}))


def _bash_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "\U0001f4bb" in ln]


# ---------------------------------------------------------------------------
# SC-1: Default config parity (will fail until T4 updates defaults)
# ---------------------------------------------------------------------------


def test_default_config_bash_max_len_is_80() -> None:
    """ToolDisplayConfig() default bash_max_len must match live constant (80)."""
    assert ToolDisplayConfig().bash_max_len == 80


def test_default_config_names_threshold_is_5() -> None:
    """ToolDisplayConfig() default names_threshold must match live constant (5)."""
    assert ToolDisplayConfig().names_threshold == 5


# ---------------------------------------------------------------------------
# SC-1/SC-7: config-driven bash truncation
# ---------------------------------------------------------------------------


def test_accumulator_respects_bash_max_len_config() -> None:
    """config.bash_max_len controls truncation of bash command lines."""
    config = ToolDisplayConfig(bash_max_len=200)
    accum = ToolRecapAccumulator(config=config)
    _feed_bash(accum, "b1", "x" * 250)
    lines = format_recap_lines(accum, done=True)
    blines = _bash_lines(lines)
    assert blines, "Expected at least one bash recap line"
    for line in blines:
        if "`" in line:
            inner = line.split("`")[1]
            assert len(inner) <= 200, (
                f"bash inner exceeds bash_max_len=200: len={len(inner)}"
            )


def test_accumulator_uses_default_bash_max_len_when_no_config() -> None:
    """When no config supplied, ToolDisplayConfig() defaults apply (80)."""
    accum = ToolRecapAccumulator(config=ToolDisplayConfig())
    _feed_bash(accum, "b1", "y" * 200)
    lines = format_recap_lines(accum, done=True)
    blines = _bash_lines(lines)
    assert blines, "Expected at least one bash recap line"
    for line in blines:
        if "`" in line:
            inner = line.split("`")[1]
            assert len(inner) <= 80, (
                f"bash inner exceeds default bash_max_len=80: len={len(inner)}"
            )


# ---------------------------------------------------------------------------
# SC-1: group_threshold config
# ---------------------------------------------------------------------------


def test_accumulator_respects_group_threshold() -> None:
    """config.group_threshold controls when bash cmds collapse to a summary."""
    config = ToolDisplayConfig(group_threshold=10)

    # 9 commands — below threshold → individual lines, no grouping
    accum_below = ToolRecapAccumulator(config=config)
    for idx in range(9):
        _feed_bash(accum_below, f"b{idx}", f"cmd{idx}")
    lines_below = format_recap_lines(accum_below, done=True)
    blines_below = _bash_lines(lines_below)
    assert not any("commands" in ln for ln in blines_below), (
        "9 commands below group_threshold=10 must not be grouped"
    )

    # 11 commands — above threshold → grouped summary
    accum_above = ToolRecapAccumulator(config=config)
    for idx in range(11):
        _feed_bash(accum_above, f"c{idx}", f"cmd{idx}")
    lines_above = format_recap_lines(accum_above, done=True)
    blines_above = _bash_lines(lines_above)
    assert any("commands" in ln for ln in blines_above), (
        "11 commands above group_threshold=10 must be grouped"
    )


# ---------------------------------------------------------------------------
# SC-1: names_threshold config
# ---------------------------------------------------------------------------


def test_accumulator_respects_names_threshold() -> None:
    """config.names_threshold controls when file edits switch to count mode."""
    config = ToolDisplayConfig(names_threshold=2)

    # 3rd edit on same file (count=3 > threshold=2) → edits list cleared
    accum = ToolRecapAccumulator(config=config)
    _feed_file(accum, "e1", "src/a.py")
    _feed_file(accum, "e2", "src/a.py")
    _feed_file(accum, "e3", "src/a.py")
    summary = accum.files.get("src/a.py")
    assert summary is not None
    assert summary.count == 3
    assert summary.edits == [], (
        "edits list must be cleared when count exceeds names_threshold=2"
    )

    # 2 edits on same file → at threshold → edits still shown
    accum2 = ToolRecapAccumulator(config=config)
    _feed_file(accum2, "f1", "src/b.py")
    _feed_file(accum2, "f2", "src/b.py")
    summary2 = accum2.files.get("src/b.py")
    assert summary2 is not None
    assert summary2.count == 2
    assert len(summary2.edits) == 2, (
        "edits list must be intact when count == names_threshold=2"
    )


# ---------------------------------------------------------------------------
# SC-2/SC-7: show-map suppression tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,show_key,args_json,recap_marker",
    [
        (
            "web_fetch",
            "web_fetch",
            '{"url": "https://example.com"}',
            "\U0001f310",
        ),
        (
            "web_search",
            "web_search",
            '{"query": "hello world"}',
            "\U0001f310",
        ),
        (
            "bash",
            "bash",
            '{"command": "ls -la"}',
            "\U0001f4bb",
        ),
        (
            "edit",
            "edit",
            '{"path": "src/foo.py"}',
            "\U0001f527",
        ),
    ],
)
def test_route_suppresses_when_show_key_false(
    tool_name: str,
    show_key: str,
    args_json: str,
    recap_marker: str,
) -> None:
    """config.show[key]=False must suppress the corresponding recap lines."""
    config = ToolDisplayConfig(show={show_key: False})
    accum = ToolRecapAccumulator(config=config)
    _feed_tool(accum, "t1", tool_name, args_json)
    lines = format_recap_lines(accum, done=True)
    matching = [ln for ln in lines if recap_marker in ln]
    assert not matching, (
        f"show[{show_key!r}]=False must suppress lines with {recap_marker!r}; "
        f"got: {lines}"
    )


# ---------------------------------------------------------------------------
# SC-2: default show-map behaviour
# ---------------------------------------------------------------------------


def test_route_default_hides_read_grep_glob() -> None:
    """Default show has read/grep/glob=False → silent counters, no recap lines."""
    accum = ToolRecapAccumulator(config=ToolDisplayConfig())
    _feed_tool(accum, "r1", "read", '{"path": "src/foo.py"}')
    _feed_tool(accum, "r2", "grep", '{"pattern": "class Foo"}')
    _feed_tool(accum, "r3", "glob", '{"pattern": "**/*.py"}')

    # Silent counters must have incremented
    silent = accum.snapshot_silent()
    assert silent.reads == 1, f"Expected 1 silent read, got {silent.reads}"
    assert silent.greps == 1, f"Expected 1 silent grep, got {silent.greps}"
    assert silent.globs == 1, f"Expected 1 silent glob, got {silent.globs}"

    # No bash/file/web detail lines — only the magnifier (silent counts) line
    lines = format_recap_lines(accum, done=True)
    assert not any("\U0001f4bb" in ln for ln in lines), "No bash lines expected"
    assert not any("\U0001f310" in ln for ln in lines), "No web lines expected"
    assert not any("\U0001f527 `" in ln for ln in lines), (
        "No file-edit detail lines expected"
    )
    assert any("\U0001f50d" in ln for ln in lines), (
        "Silent-count line must appear when read/grep/glob calls exist"
    )


def test_route_default_shows_bash_edit_write_web_fetch() -> None:
    """Default show has bash/edit/write/web_fetch=True → recap lines emitted."""
    accum = ToolRecapAccumulator(config=ToolDisplayConfig())
    _feed_bash(accum, "b1", "ls -la")
    _feed_file(accum, "e1", "src/foo.py", "edit")
    _feed_file(accum, "w1", "src/bar.py", "write")
    _feed_tool(accum, "wf1", "web_fetch", '{"url": "https://example.com"}')

    lines = format_recap_lines(accum, done=True)
    assert any("\U0001f4bb" in ln for ln in lines), (
        "bash must produce a recap line by default"
    )
    assert any("\U0001f527" in ln for ln in lines), (
        "edit/write must produce recap lines by default"
    )
    assert any("\U0001f310" in ln for ln in lines), (
        "web_fetch must produce a recap line by default"
    )
