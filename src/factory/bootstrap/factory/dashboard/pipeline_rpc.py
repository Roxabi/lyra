"""Hub-side pipeline list RPC for factory-dashboard BFF."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.bootstrap.pipeline_ingest import sync_m1_deploy_from_fleet
from roxabi_contracts.dashboard import (
    DashboardPipelineCheck,
    DashboardPipelineResponse,
    DashboardPipelineRun,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub


async def handle_pipeline_list(
    hub: Hub, nc: NATS, _payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    store = getattr(hub, "_pipeline_store", None)
    if store is None:
        return DashboardPipelineResponse(runs=[]).model_dump()
    sync_m1_deploy_from_fleet(hub, store)
    runs = [
        DashboardPipelineRun(
            repo=row.repo,
            pr_number=row.pr_number,
            title=row.title,
            head_sha=row.head_sha,
            head_ref=row.head_ref,
            html_url=row.html_url,
            reviewed=row.reviewed,
            open=row.open,
            ci_status=row.ci_status,
            merge_status=row.merge_status,
            publish_status=row.publish_status,
            m1_deploy_status=row.m1_deploy_status,
            cf_deploy_status=row.cf_deploy_status,
            checks=[
                DashboardPipelineCheck(
                    name=c.name,
                    status=c.status,
                    conclusion=c.conclusion,
                )
                for c in row.checks
            ],
            last_event_at=row.last_event_at,
            updated_at=row.updated_at,
        )
        for row in store.list_runs()
    ]
    return DashboardPipelineResponse(runs=runs).model_dump()