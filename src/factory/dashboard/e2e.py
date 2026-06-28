"""E2E stub mode for dashboard — no NATS required."""

from __future__ import annotations

import os

from roxabi_contracts.dashboard import (
    AgentHealth,
    AgentHealthResponse,
    DashboardSession,
    DashboardSessionsListResponse,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsResponse,
    DashboardTurn,
)


def e2e_enabled() -> bool:
    return os.environ.get("FACTORY_DASHBOARD_E2E", "").strip() in {"1", "true", "yes"}


def stub_agents_status(agents: list[str]) -> AgentHealthResponse:
    return AgentHealthResponse(
        agents=[
            AgentHealth(
                agent=name,
                in_roster=True,
                harness="claude-cli",
                harness_reachable=True,
                online=True,
            )
            for name in agents
        ]
    )


def stub_sessions_list(agent: str) -> DashboardSessionsListResponse:
    return DashboardSessionsListResponse(
        sessions=[
            DashboardSession(
                session_id="e2e-sess-1",
                pool_id=f"web:smoke:agent:{agent}",
                platform="web",
                cli_session_id="e2e-cli-1",
                first_user_msg="Hello from E2E stub",
                turn_count=2,
                last_active_at="2026-06-28T12:00:00+00:00",
            ),
            DashboardSession(
                session_id="e2e-sess-2",
                pool_id="telegram:main:chat:42",
                platform="telegram",
                cli_session_id="e2e-cli-2",
                first_user_msg="TG session stub",
                turn_count=5,
                last_active_at="2026-06-27T18:00:00+00:00",
            ),
        ]
    )


def stub_resume() -> DashboardSessionsResumeResponse:
    return DashboardSessionsResumeResponse(accepted=True, message="E2E resume stub")


def stub_sessions_turns(session_id: str) -> DashboardSessionsTurnsResponse:
    return DashboardSessionsTurnsResponse(
        turns=[
            DashboardTurn(
                role="user",
                content="Hello from E2E stub",
                timestamp="2026-06-28T12:00:00+00:00",
            ),
            DashboardTurn(
                role="assistant",
                content="Stub assistant reply for session replay.",
                timestamp="2026-06-28T12:00:01+00:00",
            ),
        ]
    )