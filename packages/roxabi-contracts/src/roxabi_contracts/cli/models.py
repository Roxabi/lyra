"""CliPool NATS contract models — hub ↔ clipool-worker (ADR-054)."""

from __future__ import annotations

from typing import Literal

from roxabi_contracts.envelope import ContractEnvelope

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


class CliChunkEvent(ContractEnvelope):
    """Clipool -> hub: one streaming chunk or terminal event."""

    pool_id: str
    event_type: Literal["text", "tool_use", "session_id", "result", "error"]
    text: str | None = None
    session_id: str | None = None
    is_error: bool = False
    done: bool = False


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
