"""CliPool NATS contract models — hub ↔ clipool-worker
(ADR-054 (absorbed into ADR-055))."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field

from roxabi_contracts.envelope import ContractEnvelope, WorkEnvelope
from roxabi_contracts.errors import WorkerError

__all__ = [
    "CliCmdPayload",
    "CliChunkEvent",
    "CliControlAck",
    "CliControlCmd",
    "CliHeartbeat",
]


class CliCmdPayload(WorkEnvelope):
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


class CliChunkEvent(WorkEnvelope):
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
    # Set on the first chunk of a resumed turn so the hub can distinguish
    # "resume applied" (True) from "cold-start" (False) in mixed-version rollouts.
    resumed: bool | None = None


class CliControlCmd(WorkEnvelope):
    """Hub -> clipool: control operation (reset, resume, cwd switch).

    The ``resume_and_reset`` op literal is DEPRECATED (one-minor tolerance).
    Producers must migrate to the embedded ``CliCmdPayload.resume_session_id``
    field. The worker tolerance branch is retained for one minor; mechanical
    removal is tracked in the follow-up issue (sibling under epic #1044,
    blocked-by #1009).
    """

    pool_id: str
    op: Literal["reset", "resume_and_reset", "switch_cwd"]
    cli_session_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("cli_session_id", "session_id"),
        serialization_alias="session_id",
    )
    cwd: str | None = None


class CliControlAck(WorkEnvelope):
    """Clipool -> hub: reply to CliControlCmd."""

    pool_id: str
    ok: bool
    resumed: bool | None = None


class CliHeartbeat(ContractEnvelope):
    """Clipool -> hub: periodic heartbeat."""

    worker_id: str
    pool_count: int
