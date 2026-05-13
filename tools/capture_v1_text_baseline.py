"""Capture v1 StreamProcessor output as a golden-baseline JSON fixture.

Run once from the repo root:

    uv run python tools/capture_v1_text_baseline.py

Writes (idempotently):
    tests/core/fixtures/__init__.py         (empty marker)
    tests/core/fixtures/v1_text_stream_baseline.json

Normalization rules (also documented in the JSON under "_normalization"):
- run_id fields (UUID4 or "synthetic-<UUID4>"):  replaced with "<run_id>"
  Pattern: any string matching the regex in _normalize_run_id()
- No other fields vary between runs.

T6 parity assertion MUST apply identical normalization before diffing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Normalization helpers — import by T6 for identical treatment
# ---------------------------------------------------------------------------

# Matches both raw UUID4 and the "synthetic-<uuid4>" prefix that StreamProcessor
# uses when TraceContext.get_trace_id() returns None.
_RUN_ID_RE = re.compile(
    r"synthetic-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_RUN_ID_PLACEHOLDER = "<run_id>"


def normalize_run_id(value: str) -> str:
    """Replace any UUID4 / synthetic-UUID4 with the stable placeholder."""
    return _RUN_ID_RE.sub(_RUN_ID_PLACEHOLDER, value)


def normalize_event_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *d* with run_id values replaced by the placeholder."""
    out = dict(d)
    if "run_id" in out and isinstance(out["run_id"], str):
        out["run_id"] = normalize_run_id(out["run_id"])
    return out


# ---------------------------------------------------------------------------
# Repo bootstrap — add src/ to sys.path so we import lyra without installing
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lyra.core.messaging.events import (  # noqa: E402
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.messaging.tool_display_config import (  # noqa: E402
    ToolDisplayConfig,
)
from lyra.core.processors.stream_processor import StreamProcessor  # noqa: E402

# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _aiter(*events):
    """Yield events from a plain tuple as an AsyncIterator."""
    for e in events:
        yield e


async def _collect(gen) -> list[dict[str, Any]]:
    """Drain an async generator of RenderEvents into serializable dicts."""
    results: list[dict[str, Any]] = []
    async for event in gen:
        d = dataclasses.asdict(event)
        d["type"] = type(event).__name__
        d = normalize_event_dict(d)
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def capture_single_block(config: ToolDisplayConfig) -> list[dict[str, Any]]:
    """Scenario 1: pure text turn, no tool calls."""
    sp = StreamProcessor(config, show_intermediate=False)
    events_in = _aiter(
        TextLlmEvent(text="Hello "),
        TextLlmEvent(text="world."),
        ResultLlmEvent(is_error=False, duration_ms=100),
    )
    return await _collect(sp.process(events_in))


async def capture_multi_block(config: ToolDisplayConfig) -> list[dict[str, Any]]:
    """Scenario 2: text → tool call → text → result."""
    sp = StreamProcessor(config, show_intermediate=False)
    events_in = _aiter(
        TextLlmEvent(text="Before tool "),
        ToolUseLlmEvent(
            tool_name="bash", tool_id="tool-abc123", input={"command": "ls"}
        ),
        ToolUseEndLlmEvent(tool_id="tool-abc123"),
        TextLlmEvent(text="After tool."),
        ResultLlmEvent(is_error=False, duration_ms=200),
    )
    return await _collect(sp.process(events_in))


async def capture_error(config: ToolDisplayConfig) -> list[dict[str, Any]]:
    """Scenario 3: partial text then error result."""
    sp = StreamProcessor(config, show_intermediate=False)
    events_in = _aiter(
        TextLlmEvent(text="Partial "),
        ResultLlmEvent(
            is_error=True, duration_ms=50, error_text="Something went wrong"
        ),
    )
    return await _collect(sp.process(events_in))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    fixtures_dir = _REPO_ROOT / "tests" / "core" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    init_py = fixtures_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text("")

    # Use default config — throttle_ms=0 to avoid time-based suppression in tests
    config = ToolDisplayConfig(throttle_ms=0)

    single_block = await capture_single_block(config)
    multi_block = await capture_multi_block(config)
    error = await capture_error(config)

    payload: dict[str, Any] = {
        "_normalization": (
            "run_id fields are replaced with '<run_id>' using the regex in "
            "tools/capture_v1_text_baseline.py::normalize_run_id(). "
            "T6 parity assertion must apply the same normalization before diffing. "
            "show_intermediate=False is used so TextRenderEvent chunks are NOT emitted "
            "per-chunk (only the final is_final=True event is emitted). "
            "ToolDisplayConfig(throttle_ms=0) disables the throttle so "
            "ToolSummaryRenderEvent is emitted deterministically on every tool call."
        ),
        "single_block": single_block,
        "multi_block": multi_block,
        "error": error,
    }

    out_path = fixtures_dir / "v1_text_stream_baseline.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Written: {out_path}")
    print(f"  single_block: {len(single_block)} events")
    print(f"  multi_block:  {len(multi_block)} events")
    print(f"  error:        {len(error)} events")


if __name__ == "__main__":
    asyncio.run(main())
