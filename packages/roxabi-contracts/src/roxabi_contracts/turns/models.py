"""Turn-write event models. Canonical subject: ``lyra.turns.write``."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from roxabi_contracts.envelope import ContractEnvelope

# Forward-compat config for nested payload classes. Top-level envelope
# (TurnWriteEvent) inherits extra="ignore" from ContractEnvelope; we mirror
# the invariant on the discriminated-union members so an unknown field added
# to a newer payload version parses cleanly on an older consumer.
_PAYLOAD_CONFIG = ConfigDict(extra="ignore")


# --- Payloads (one per TurnWriteEvent.kind) ---


class LogTurnPayload(BaseModel):
    model_config = _PAYLOAD_CONFIG

    kind: Literal["log_turn"] = "log_turn"
    role: Literal["user", "assistant"]
    content: str
    # Natural dedupe key — UNIQUE(platform, message_id) on conversation_turns.
    # None for assistant turns that have no platform message_id.
    message_id: str | None = None
    reply_message_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class StartSessionPayload(BaseModel):
    model_config = _PAYLOAD_CONFIG

    kind: Literal["start_session"] = "start_session"


class EndSessionPayload(BaseModel):
    model_config = _PAYLOAD_CONFIG

    kind: Literal["end_session"] = "end_session"


class SetCliSessionPayload(BaseModel):
    model_config = _PAYLOAD_CONFIG

    kind: Literal["set_cli_session"] = "set_cli_session"
    cli_session_id: str


class IncrementResumeCountPayload(BaseModel):
    model_config = _PAYLOAD_CONFIG

    kind: Literal["increment_resume_count"] = "increment_resume_count"
    target_count: int  # high-water mark (computed at publish: current+1)


TurnWritePayload = Annotated[
    Union[
        LogTurnPayload,
        StartSessionPayload,
        EndSessionPayload,
        SetCliSessionPayload,
        IncrementResumeCountPayload,
    ],
    Field(discriminator="kind"),
]

# --- Envelope ---


class TurnWriteEvent(ContractEnvelope):
    """Event published to lyra.turns.write.

    Idempotence strategy (per kind):
      - log_turn: UNIQUE(platform, message_id) on conversation_turns.
      - start/end/set_cli: naturally idempotent (INSERT OR IGNORE
        / UPDATE WHERE / INSERT OR REPLACE).
      - increment_resume_count: high-water mark on target_count
        + processed_events(event_id) catch.
    """

    event_id: UUID = Field(default_factory=uuid4)
    pool_id: str
    session_id: str
    platform: str
    user_id: str
    timestamp: datetime
    payload: TurnWritePayload

    @property
    def kind(self) -> str:
        return self.payload.kind
