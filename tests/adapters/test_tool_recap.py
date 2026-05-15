"""RED tests for ToolRecapAccumulator + format_recap_lines (#1214 T1+T2).

Module under test does NOT exist yet — these tests define the contract.
Expected failure: ImportError on ToolRecapAccumulator.
"""

from __future__ import annotations

import json

from lyra.adapters.shared._tool_recap import (  # type: ignore[import-untyped]
    ToolRecapAccumulator,
    format_recap_lines,
)

from lyra.core.messaging.render_events import (
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _drive(
    accum: ToolRecapAccumulator,
    calls: list[tuple[str, dict]],
    *,
    id_prefix: str = "tc",
) -> None:
    """Feed Start → Args(delta=full_json) → End for each (tool_name, args) tuple."""
    for i, (tool_name, args) in enumerate(calls):
        tid = f"{id_prefix}-{i}"
        accum.observe_start(
            ToolCallStartRenderEvent(tool_call_id=tid, tool_name=tool_name)
        )
        accum.observe_args(
            ToolCallArgsRenderEvent(tool_call_id=tid, delta=json.dumps(args))
        )
        accum.observe_end(ToolCallEndRenderEvent(tool_call_id=tid))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_format_lines_files_bash_web_silent_canonical_order() -> None:
    """Lines must come out in canonical order: header, files, bash, web_fetch,
    web_search, agent, unknown, silent."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("Edit", {"path": "/a/b.py", "old_string": "x", "new_string": "y"}),
            ("Bash", {"command": "ls -la"}),
            ("WebFetch", {"url": "https://example.com"}),
            ("WebSearch", {"query": "pytest fixtures"}),
            ("Agent", {"description": "run sub-agent"}),
        ],
    )
    # 4 reads via individual observe_start/args/end for read tool
    for i in range(4):
        tid = f"read-{i}"
        accum.observe_start(
            ToolCallStartRenderEvent(tool_call_id=tid, tool_name="Read")
        )
        accum.observe_args(
            ToolCallArgsRenderEvent(
                tool_call_id=tid, delta=json.dumps({"file_path": f"/f{i}"})
            )
        )
        accum.observe_end(ToolCallEndRenderEvent(tool_call_id=tid))

    lines = format_recap_lines(accum, done=True)

    assert lines[0] == "🔧 Done ✅"
    # Find section positions
    file_idx = next(i for i, ln in enumerate(lines) if ln.startswith("✏️"))
    bash_idx = next(i for i, ln in enumerate(lines) if ln.startswith("💻"))
    fetch_idx = next(i for i, ln in enumerate(lines) if "example.com" in ln)
    search_idx = next(i for i, ln in enumerate(lines) if "pytest fixtures" in ln)
    agent_idx = next(i for i, ln in enumerate(lines) if ln.startswith("🤖"))
    silent_idx = next(i for i, ln in enumerate(lines) if ln.startswith("🔍"))

    assert file_idx < bash_idx < fetch_idx < search_idx < agent_idx < silent_idx


def test_format_lines_unknown_tool_sorted_alpha() -> None:
    """Unknown tools are sorted alphabetically by tool name."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("TodoWrite", {"todos": []}),
            ("TodoWrite", {"todos": []}),
            ("TodoWrite", {"todos": []}),
            ("LS", {"path": "/"}),
        ],
    )
    lines = format_recap_lines(accum, done=True)
    assert lines == ["🔧 Done ✅", "🔧 1 ls", "🔧 3 todowrite"]


def test_format_lines_empty_accum_returns_empty_list() -> None:
    """Fresh accumulator with no events → empty list (no header)."""
    accum = ToolRecapAccumulator()
    assert format_recap_lines(accum, done=True) == []


def test_format_lines_is_error_result_counted_identically() -> None:
    """ToolCallResultRenderEvent(is_error=True) is ignored — output identical
    to success case for same one-bash-call turn."""
    accum_ok = ToolRecapAccumulator()
    _drive(accum_ok, [("Bash", {"command": "echo hi"})])

    accum_err = ToolRecapAccumulator()
    _drive(accum_err, [("Bash", {"command": "echo hi"})])
    # Send a result event with is_error=True; accumulator must ignore it
    if hasattr(accum_err, "observe_result"):
        accum_err.observe_result(
            ToolCallResultRenderEvent(
                tool_call_id="tc-0", content="boom", is_error=True
            )
        )

    assert format_recap_lines(accum_ok, done=True) == format_recap_lines(
        accum_err, done=True
    )


def test_files_collapse_threshold_three() -> None:
    """3 edit calls on 3 different paths → single collapsed line."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("Edit", {"path": "/a.py", "old_string": "x", "new_string": "y"}),
            ("Edit", {"path": "/b.py", "old_string": "x", "new_string": "y"}),
            ("Edit", {"path": "/c.py", "old_string": "x", "new_string": "y"}),
        ],
    )
    lines = format_recap_lines(accum, done=True)
    file_lines = [ln for ln in lines if ln.startswith("✏️")]
    assert len(file_lines) == 1
    assert "3 files" in file_lines[0]
    # edits count or edit labels present
    assert "3" in file_lines[0]


def test_bash_collapse_threshold_three() -> None:
    """3 bash commands → single collapsed line."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("Bash", {"command": "ls"}),
            ("Bash", {"command": "pwd"}),
            ("Bash", {"command": "echo hi"}),
        ],
    )
    lines = format_recap_lines(accum, done=True)
    bash_lines = [ln for ln in lines if ln.startswith("💻")]
    assert bash_lines == ["💻 3 commands"]


def test_silent_counts_omitted_when_all_zero() -> None:
    """No read/grep/glob → no 🔍 line."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("Edit", {"path": "/a.py", "old_string": "x", "new_string": "y"}),
            ("Bash", {"command": "ls"}),
        ],
    )
    lines = format_recap_lines(accum, done=True)
    assert not any(ln.startswith("🔍") for ln in lines)


def test_web_fetch_and_web_search_in_separate_sections() -> None:
    """web_fetch and web_search emit in fetch-first, search-second order."""
    accum = ToolRecapAccumulator()
    _drive(
        accum,
        [
            ("WebFetch", {"url": "https://a.com"}),
            ("WebSearch", {"query": "hello"}),
        ],
    )
    lines = format_recap_lines(accum, done=True)
    # Strip header
    content_lines = lines[1:]
    assert content_lines == ["🌐 https://a.com", "🌐 hello"]


def test_done_true_emits_done_header_else_working() -> None:
    """done=False → 'Working…', done=True → 'Done ✅'."""
    accum = ToolRecapAccumulator()
    _drive(accum, [("Bash", {"command": "ls"})])

    working_lines = format_recap_lines(accum, done=False)
    done_lines = format_recap_lines(accum, done=True)

    assert working_lines[0] == "🔧 Working…"
    assert done_lines[0] == "🔧 Done ✅"
