"""Pipeline read-model row types and shared helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

PipelineStageStatus = Literal[
    "pending",
    "running",
    "success",
    "failure",
    "skipped",
    "unknown",
    "n/a",
]

DEFAULT_REPO = "Roxabi/roxabi-factory"

M1_DEPLOY_QUORUM = frozenset(
    {
        "factory-hub",
        "factory-telegram",
        "factory-discord",
        "factory-dashboard",
        "factory-clipool",
    }
)


@dataclass(slots=True)
class PipelineCheckRow:
    name: str
    status: str
    conclusion: str | None = None


@dataclass(slots=True)
class PipelineRunRow:
    repo: str
    pr_number: int
    title: str
    head_sha: str | None
    head_ref: str | None
    html_url: str | None
    reviewed: bool
    open: bool
    ci_status: PipelineStageStatus
    merge_status: PipelineStageStatus
    publish_status: PipelineStageStatus
    m1_deploy_status: PipelineStageStatus
    cf_deploy_status: PipelineStageStatus
    publish_sha: str | None
    checks: list[PipelineCheckRow] = field(default_factory=list)
    last_event_at: str | None = None
    updated_at: str | None = None


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def worst_ci(checks: list[PipelineCheckRow]) -> PipelineStageStatus:
    if not checks:
        return "unknown"
    conclusions = {c.conclusion or c.status for c in checks}
    if "failure" in conclusions or "cancelled" in conclusions:
        return "failure"
    statuses = {c.status for c in checks}
    if statuses & {"queued", "in_progress", "pending"}:
        return "running"
    if conclusions <= {"success", "skipped", "neutral"} or statuses <= {
        "completed",
        "success",
    }:
        return "success"
    return "unknown"


def str_or_none(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def pr_has_reviewed_label(labels: object) -> bool:
    if not isinstance(labels, list):
        return False
    for item in labels:
        if item == "reviewed":
            return True
        if isinstance(item, dict) and item.get("name") == "reviewed":
            return True
    return False