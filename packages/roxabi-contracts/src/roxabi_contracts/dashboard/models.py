"""Dashboard BFF wire models (#1771)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HarnessKind = Literal["claude-cli", "omp-rpc"]
SseEventType = Literal["delta", "done", "error", "ping"]
PlatformTag = Literal["telegram", "discord", "web"]


class ChatRequest(BaseModel):
    agent: str
    text: str
    session_id: str | None = None
    harness: HarnessKind = "claude-cli"
    model: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    stream_token: str
    accepted: bool = True


class SseEvent(BaseModel):
    type: SseEventType
    text: str | None = None
    message: str | None = None


class AgentHealth(BaseModel):
    agent: str
    in_roster: bool
    harness: HarnessKind
    harness_reachable: bool
    online: bool


class AgentHealthResponse(BaseModel):
    agents: list[AgentHealth]


class DashboardSession(BaseModel):
    session_id: str
    pool_id: str
    platform: PlatformTag
    cli_session_id: str | None = None
    first_user_msg: str | None = None
    turn_count: int = 0
    last_active_at: str


class DashboardSessionsListRequest(BaseModel):
    agent: str
    limit: int = Field(default=20, ge=1, le=50)


class DashboardSessionsListResponse(BaseModel):
    sessions: list[DashboardSession]


class DashboardSessionsResumeRequest(BaseModel):
    cli_session_id: str
    agent: str


class DashboardSessionsResumeResponse(BaseModel):
    accepted: bool
    message: str = ""


class DashboardTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: str


class DashboardSessionsTurnsRequest(BaseModel):
    session_id: str
    limit: int = Field(default=200, ge=1, le=500)


class DashboardSessionsTurnsResponse(BaseModel):
    turns: list[DashboardTurn]


class DashboardJob(BaseModel):
    job_id: str
    pool_id: str
    agent: str | None = None
    platform: PlatformTag | str | None = None
    status: Literal["open", "closing"] | str
    started_at: str
    concurrency_mode: str
    worker_loc: str | None = None
    steer_subject: str


class DashboardJobsListResponse(BaseModel):
    jobs: list[DashboardJob]