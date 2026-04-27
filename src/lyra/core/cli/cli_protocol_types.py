"""Shared types and helpers for the Claude CLI NDJSON protocol layer.

Extracted from cli_protocol.py to break the circular import between
cli_protocol (re-export facade) and cli_non_streaming / cli_streaming
(protocol implementations).  Consumers should import from cli_protocol
for the full public surface, or from this module when only the types
are needed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass

from ..agent.agent_config import ModelConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


async def _read_stderr_snippet(
    proc: asyncio.subprocess.Process, limit: int = 512
) -> str:
    """Read up to *limit* bytes from proc.stderr without blocking.

    Returns a stripped string, or "" if nothing is available.
    """
    if proc.stderr is None:
        return ""
    try:
        raw = await asyncio.wait_for(proc.stderr.read(limit), timeout=0.5)
        return raw.decode(errors="replace").strip()
    except (asyncio.TimeoutError, Exception):
        return ""


def build_cmd(
    model_config: ModelConfig,
    session_id: str | None = None,
    system_prompt: str = "",
) -> tuple[list[str], str | None]:
    """Build the ``claude`` CLI command list from *model_config*.

    Returns ``(cmd, prompt_file)`` where *prompt_file* is a temp path
    that the caller must clean up, or ``None`` when no system prompt
    was written.
    """
    cmd = [
        "claude",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model_config.model,
        # max_turns=None → unlimited; 0 is DB sentinel for None.
        *(
            ["--max-turns", str(model_config.max_turns)]
            if model_config.max_turns
            else []
        ),
    ]
    if model_config.streaming:
        cmd.append("--include-partial-messages")
    if model_config.skip_permissions:
        log.warning(
            "SECURITY: --dangerously-skip-permissions enabled for CLI subprocess"
        )
        cmd.append("--dangerously-skip-permissions")
    if model_config.tools:
        cmd.extend(["--allowedTools", ",".join(model_config.tools)])
    prompt_file: str | None = None
    if system_prompt:
        fd, prompt_file = tempfile.mkstemp(suffix=".txt", prefix="lyra-prompt-")
        try:
            os.write(fd, system_prompt.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(prompt_file, 0o600)
        cmd.extend(["--system-prompt-file", prompt_file])
    if session_id:
        cmd.extend(["--resume", session_id])
    return cmd, prompt_file


# Validate Claude session IDs (strict UUID format).
SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# Private alias kept for backward compat (cli_pool.py imports this name).
_SESSION_ID_RE = SESSION_ID_RE


@dataclass
class CliResult:
    """Result from a CliPool.send() call.

    Exactly one of ``result`` or ``error`` will be set (not both).
    ``warning`` is set when the response was truncated (e.g. max_turns reached).
    ``session_id`` is always present on success.
    """

    result: str = ""
    session_id: str = ""
    error: str = ""
    warning: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class CliProtocolOptions:
    """Wire-level timing and retry options for the NDJSON protocol.

    Carries the three ``[cli_pool]`` knobs from ``config.toml`` into the
    protocol layer.  Defaults mirror pre-#369 hardcoded values.
    """

    stdin_drain_timeout: float = 10.0
    max_idle_retries: int = 3
    intermediate_timeout: float = 5.0
