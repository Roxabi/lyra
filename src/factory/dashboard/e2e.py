"""E2E stub mode for dashboard — no NATS required."""

from __future__ import annotations

import os

from roxabi_contracts.dashboard import (
    AgentHealth,
    AgentHealthResponse,
    DashboardJob,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerResponse,
    DashboardOpsHealthResponse,
    DashboardOpsLogsResponse,
    DashboardSession,
    DashboardSessionsListResponse,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsResponse,
    DashboardTurn,
    OpsEngineHealth,
    OpsLogEntry,
    OpsLogPreset,
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


def stub_jobs_launch(agent: str) -> DashboardJobsLaunchResponse:
    return DashboardJobsLaunchResponse(
        accepted=True,
        job_id="e2e-launch-1",
        message=f"E2E launch stub for {agent}",
        dispatch_subject="factory.jobs.omp",
    )


def stub_jobs_steer(job_id: str) -> DashboardJobsSteerResponse:
    return DashboardJobsSteerResponse(
        accepted=True,
        message=f"E2E steer stub for {job_id}",
    )


def stub_jobs_list() -> DashboardJobsListResponse:
    return DashboardJobsListResponse(
        jobs=[
            DashboardJob(
                job_id="e2e-job-1",
                pool_id="web:smoke:agent:lyra",
                agent="lyra",
                platform="web",
                status="open",
                started_at="2026-06-28T12:00:00+00:00",
                concurrency_mode="steer",
                worker_loc="clipool-worker",
                steer_subject="factory.job.e2e-job-1.steer",
            ),
            DashboardJob(
                job_id="e2e-job-2",
                pool_id="telegram:main:chat:42",
                agent="aryl",
                platform="telegram",
                status="closing",
                started_at="2026-06-28T11:30:00+00:00",
                concurrency_mode="queue",
                worker_loc=None,
                steer_subject="factory.job.e2e-job-2.steer",
            ),
        ]
    )


def stub_ops_health() -> DashboardOpsHealthResponse:
    return DashboardOpsHealthResponse(
        engines=[
            OpsEngineHealth(
                engine="loki",
                label="Loki",
                reachable=True,
                detail="E2E stub",
            ),
            OpsEngineHealth(
                engine="langfuse",
                label="Langfuse",
                reachable=True,
                detail="E2E stub",
            ),
            OpsEngineHealth(
                engine="otel-collector",
                label="OTel Collector",
                reachable=False,
                detail="E2E stub offline",
            ),
        ]
    )


def stub_ops_logs(preset: OpsLogPreset) -> DashboardOpsLogsResponse:
    return DashboardOpsLogsResponse(
        preset=preset,
        query=f"e2e-stub-{preset}",
        engine_reachable=True,
        entries=[
            OpsLogEntry(
                timestamp="2026-06-28T12:00:00+00:00",
                line=f"E2E stub log line for {preset}",
                labels={"job": "factory-journal", "systemd_unit": "factory-hub.service"},
            ),
            OpsLogEntry(
                timestamp="2026-06-28T11:59:00+00:00",
                line="Second stub entry — hub heartbeat ok",
                labels={"job": "factory-journal"},
            ),
        ],
    )


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