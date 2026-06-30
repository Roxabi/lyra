"""E2E stub mode for dashboard — no NATS required."""

from __future__ import annotations

import os

from roxabi_contracts.dashboard import (
    AgentHealth,
    AgentHealthResponse,
    ConnectorInstallationRow,
    DashboardConnectorInstallationsListResponse,
    DashboardFleetResponse,
    DashboardFleetRow,
    DashboardGithubInstallUrlResponse,
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
    HarnessKind,
    OpsEngineHealth,
    OpsLogEntry,
    OpsLogPreset,
)


def e2e_enabled() -> bool:
    return os.environ.get("FACTORY_DASHBOARD_E2E", "").strip() in {"1", "true", "yes"}


def _e2e_agent_backend(name: str) -> HarnessKind:
    return "omp-rpc" if name.lower().startswith("aryl") else "claude-cli"


def stub_agents_status(agents: list[str]) -> AgentHealthResponse:
    return AgentHealthResponse(
        agents=[
            AgentHealth(
                agent=name,
                in_roster=True,
                harness=_e2e_agent_backend(name),
                harness_reachable=name.lower().startswith("lyr"),
                online=name.lower().startswith("lyr"),
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
                labels={
                    "job": "factory-journal",
                    "systemd_unit": "factory-hub.service",
                },
            ),
            OpsLogEntry(
                timestamp="2026-06-28T11:59:00+00:00",
                line="Second stub entry — hub heartbeat ok",
                labels={"job": "factory-journal"},
            ),
        ],
    )


def stub_fleet() -> DashboardFleetResponse:
    return DashboardFleetResponse(
        rows=[
            DashboardFleetRow(
                container_name="factory-hub",
                host="roxabituwer",
                component_key="hub",
                image_ref="ghcr.io/roxabi/factory:staging-svc",
                image_revision="e2e-sha",
                health="healthy",
                status="ok",
                last_report_at="2026-06-29T12:00:00+00:00",
                age_s=12.0,
                systemd_unit="factory-hub.service",
                instrumented=True,
                source="live",
            ),
            DashboardFleetRow(
                container_name="factory-loki",
                component_key="loki",
                image_ref="grafana/loki:3.0.0",
                health="unknown",
                status="unknown",
                systemd_unit="factory-loki.service",
                instrumented=False,
                source="manifest",
            ),
            DashboardFleetRow(
                container_name="factory-clipool",
                component_key="clipool",
                image_ref="ghcr.io/roxabi/factory:staging",
                health="healthy",
                status="stale",
                age_s=120.0,
                systemd_unit="factory-clipool.service",
                instrumented=True,
                source="manifest",
            ),
        ]
    )


def stub_github_install_url() -> DashboardGithubInstallUrlResponse:
    return DashboardGithubInstallUrlResponse(
        url="https://github.com/apps/factory-e2e/installations/new",
        app_slug="factory-e2e",
    )


def stub_connector_installations(
    connector: str, factory_tenant: str
) -> dict[str, object]:
    return DashboardConnectorInstallationsListResponse(
        installations=[
            ConnectorInstallationRow(
                connector=connector,
                external_id="4242",
                factory_tenant=factory_tenant,
                enabled=True,
            )
        ]
    ).model_dump()


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