"""CliPool NATS contract models — hub ↔ clipool-worker (ADR-054)."""

from __future__ import annotations

from typing import Literal

from roxabi_contracts.envelope import ContractEnvelope
from roxabi_contracts.errors import WorkerError

__all__ = [
    "CliCmdPayload",
    "CliChunkEvent",
    "CliControlAck",
    "CliControlCmd",
    "CliHeartbeat",
]


class CliCmdPayload(ContractEnvelope):
    """Hub -> clipool: run a claude-cli command."""

    pool_id: str
    lyra_session_id: str
    text: str
    model_cfg: dict
    system_prompt: str
    resume_session_id: str | None = None
    stream: bool = True
    agent_name: str | None = None
    agent_email: str | None = None


class CliChunkEvent(ContractEnvelope):
    """Clipool -> hub: one streaming chunk or terminal event."""

    pool_id: str
    event_type: Literal["text", "tool_use", "session_id", "result", "error"]
    text: str | None = None
    tool_name: str | None = None  # tool name for tool_use events
    tool_id: str | None = None  # tool call id for tool_use events
    tool_input: dict | None = None  # tool input dict for tool_use events
    session_id: str | None = None
    is_error: bool = False
    done: bool = False
    worker_error: WorkerError | None = None


class CliControlCmd(ContractEnvelope):
    """Hub -> clipool: control operation (reset, resume, cwd switch)."""

    pool_id: str
    op: Literal["reset", "resume_and_reset", "switch_cwd"]
    session_id: str | None = None
    cwd: str | None = None


class CliControlAck(ContractEnvelope):
    """Clipool -> hub: reply to CliControlCmd."""

    pool_id: str
    ok: bool
    resumed: bool | None = None


class CliHeartbeat(ContractEnvelope):
    """Clipool -> hub: periodic heartbeat."""

    worker_id: str
    pool_count: int
