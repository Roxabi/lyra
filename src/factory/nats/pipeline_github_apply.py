"""GitHub ingress event handlers for the pipeline read model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory.nats.pipeline_correlation import active_publish_pending_row
from factory.nats.pipeline_models import (
    DEFAULT_REPO,
    PipelineCheckRow,
    pr_has_reviewed_label,
    str_or_none,
    worst_ci,
)

if TYPE_CHECKING:
    from factory.nats.pipeline_store import PipelineStore


def apply_pull_request(
    store: PipelineStore,
    repo: str,
    action: str,
    payload: dict[str, object],
    kind: str,
) -> None:
    pr = payload.get("pull_request")
    if not isinstance(pr, dict) or pr.get("number") is None:
        return
    pr_number = int(pr["number"])
    reviewed = pr_has_reviewed_label(pr.get("labels"))
    if kind == "pull_request.labeled" and isinstance(payload.get("label"), dict):
        name = payload["label"].get("name")
        if name == "reviewed":
            reviewed = True
    if kind == "pull_request.unlabeled" and isinstance(payload.get("label"), dict):
        name = payload["label"].get("name")
        if name == "reviewed":
            reviewed = False

    if action == "closed":
        merged = bool(pr.get("merged"))
        row = store._upsert_pr(
            repo=repo,
            pr_number=pr_number,
            title=str(pr.get("title") or ""),
            head_sha=str_or_none(pr.get("head_sha")),
            head_ref=str_or_none(pr.get("head_ref")),
            html_url=str_or_none(pr.get("html_url")),
            open_=False,
        )
        row.reviewed = reviewed or row.reviewed
        row.merge_status = "success" if merged else "skipped"
        if merged:
            row.publish_status = "pending"
            row.m1_deploy_status = "pending"
            row.cf_deploy_status = "pending"
        else:
            row.publish_status = "n/a"
            row.m1_deploy_status = "n/a"
            row.cf_deploy_status = "n/a"
        store._touch(row)
        return

    row = store._upsert_pr(
        repo=repo,
        pr_number=pr_number,
        title=str(pr.get("title") or ""),
        head_sha=str_or_none(pr.get("head_sha")),
        head_ref=str_or_none(pr.get("head_ref")),
        html_url=str_or_none(pr.get("html_url")),
        open_=True,
    )
    row.reviewed = reviewed or row.reviewed
    if action == "synchronize" and row.head_sha:
        row.ci_status = "running"
    store._touch(row)


def apply_check_run(
    store: PipelineStore,
    repo: str,
    payload: dict[str, object],
) -> None:
    cr = payload.get("check_run")
    if not isinstance(cr, dict):
        return
    numbers = cr.get("pull_request_numbers")
    pr_numbers: list[int] = []
    if isinstance(numbers, list):
        pr_numbers = [int(n) for n in numbers if n is not None]
    if not pr_numbers:
        return
    name = str(cr.get("name") or "check")
    status = str(cr.get("status") or "unknown")
    conclusion = str_or_none(cr.get("conclusion"))
    for pr_number in pr_numbers:
        row = store._runs.get((repo, pr_number))
        if row is None:
            row = store._upsert_pr(repo=repo, pr_number=pr_number, open_=True)
        replaced = False
        for check in row.checks:
            if check.name == name:
                check.status = status
                check.conclusion = conclusion
                replaced = True
                break
        if not replaced:
            row.checks.append(
                PipelineCheckRow(name=name, status=status, conclusion=conclusion)
            )
        row.ci_status = worst_ci(row.checks)
        store._touch(row)


def apply_workflow_run(
    store: PipelineStore,
    repo: str,
    payload: dict[str, object],
) -> None:
    wr = payload.get("workflow_run")
    if not isinstance(wr, dict):
        return
    name = str(wr.get("name") or "")
    if name != "publish":
        return
    conclusion = str(wr.get("conclusion") or "")
    if conclusion not in {"success", "failure", "cancelled"}:
        return
    head_sha = str_or_none(wr.get("head_sha"))
    if conclusion == "success" and head_sha:
        store._last_publish_sha = head_sha
        if store._db is not None:
            store._db.set_last_publish_sha(head_sha)
    row = active_publish_pending_row(store._runs.values(), repo)
    if row is None:
        return
    row.publish_status = "success" if conclusion == "success" else "failure"
    if head_sha:
        row.publish_sha = head_sha
    store._touch(row)


def github_repo(payload: dict[str, object]) -> str:
    return str(payload.get("repository") or DEFAULT_REPO)