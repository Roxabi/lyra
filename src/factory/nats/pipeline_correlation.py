"""Active-release row selection for per-PR stage correlation."""

from __future__ import annotations

from collections.abc import Iterable

from factory.nats.pipeline_models import PipelineRunRow


def active_publish_pending_row(
    runs: Iterable[PipelineRunRow],
    repo: str,
) -> PipelineRunRow | None:
    candidates = [
        row
        for row in runs
        if row.repo == repo
        and not row.open
        and row.merge_status == "success"
        and row.publish_status == "pending"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.pr_number)


def active_cf_pending_row(
    runs: Iterable[PipelineRunRow],
    repo: str,
) -> PipelineRunRow | None:
    candidates = [
        row
        for row in runs
        if row.repo == repo
        and not row.open
        and row.merge_status == "success"
        and row.cf_deploy_status == "pending"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.pr_number)