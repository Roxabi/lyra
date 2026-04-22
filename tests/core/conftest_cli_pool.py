"""Shared helpers and constants for cli_pool test files."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent.agent_config import ModelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_proc(stdout_lines: list[bytes]) -> MagicMock:
    """Return a mock Process with controllable stdout readline side-effects."""
    proc = MagicMock()
    proc.returncode = None  # alive
    proc.pid = 99

    # stdin
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock(return_value=None)

    # stdout: readline returns lines in order, then b"" for EOF
    lines_with_eof = list(stdout_lines) + [b""]
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=lines_with_eof)

    # stderr: empty by default
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncMock(return_value=b"")

    # termination: wait() blocks forever for alive processes (the spawn
    # early-liveness check uses wait_for with a short timeout — it must
    # timeout for alive procs, not return immediately).
    _never_stops = asyncio.Event()

    async def _wait() -> int:
        if proc.returncode is None:
            await _never_stops.wait()  # explicit: block forever, cancelled by wait_for
        return proc.returncode or 0

    proc.terminate = MagicMock(side_effect=lambda: setattr(proc, "returncode", 0))
    proc.wait = _wait
    proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))

    return proc


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = ModelConfig()

INIT_LINE = _ndjson(
    {
        "type": "system",
        "subtype": "init",
        "session_id": "sess-1",
        "model": "claude-sonnet-4-5",
    }
)
ASSISTANT_LINE = _ndjson(
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Hello from Claude"}]},
    }
)
RESULT_LINE = _ndjson(
    {
        "type": "result",
        "result": "Hello from Claude",
        "session_id": "sess-1",
        "is_error": False,
        "duration_ms": 42,
    }
)

_PATCH_TARGET = "lyra.core.cli.cli_pool.asyncio.create_subprocess_exec"
