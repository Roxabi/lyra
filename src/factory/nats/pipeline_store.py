"""In-memory PR pipeline read model (plane ④) for dashboard /pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from factory.nats.pipeline_cf_registry import pages_branch_matches, resolve_pages_project
from factory.nats.pipeline_db import PipelineDb, default_db_path

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


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _worst_ci(checks: list[PipelineCheckRow]) -> PipelineStageStatus:
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


class PipelineStore:
    """Mutable PR pipeline projection — hub-local, not routing state."""

    def __init__(self, db_path: str | Path | None = default_db_path()) -> None:
        self._runs: dict[tuple[str, int], PipelineRunRow] = {}
        self._processed: set[str] = set()
        self._last_publish_sha: str | None = None
        self._db: PipelineDb | None = None
        if db_path is not None:
            self._db = PipelineDb(db_path)
            self._db.connect()
            self._hydrate()

    def _hydrate(self) -> None:
        if self._db is None:
            return
        for row in self._db.load_runs():
            self._runs[(row.repo, row.pr_number)] = row
        self._processed = self._db.load_processed()
        self._last_publish_sha = self._db.load_last_publish_sha()

    def has_runs(self) -> bool:
        return bool(self._runs)

    def mark_processed(self, event_id: str) -> bool:
        """Return False if this delivery was already applied."""
        if event_id in self._processed:
            return False
        self._processed.add(event_id)
        if self._db is not None:
            self._db.record_processed(event_id, _now_iso())
        elif len(self._processed) > 10_000:
            self._processed.clear()
        return True

    def list_runs(self, *, include_closed_hours: float = 24.0) -> list[PipelineRunRow]:
        rows = list(self._runs.values())
        rows.sort(
            key=lambda r: (not r.open, r.pr_number),
            reverse=True,
        )
        return rows

    def get_run(self, repo: str, pr_number: int) -> PipelineRunRow | None:
        return self._runs.get((repo, pr_number))

    def _touch(self, row: PipelineRunRow) -> None:
        now = _now_iso()
        row.last_event_at = now
        row.updated_at = now
        self._save_row(row)

    def _save_row(self, row: PipelineRunRow) -> None:
        if self._db is not None:
            self._db.upsert_run(row)

    def _upsert_pr(
        self,
        *,
        repo: str,
        pr_number: int,
        title: str = "",
        head_sha: str | None = None,
        head_ref: str | None = None,
        html_url: str | None = None,
        open_: bool = True,
    ) -> PipelineRunRow:
        key = (repo, pr_number)
        row = self._runs.get(key)
        if row is None:
            row = PipelineRunRow(
                repo=repo,
                pr_number=pr_number,
                title=title or f"PR #{pr_number}",
                head_sha=head_sha,
                head_ref=head_ref,
                html_url=html_url,
                reviewed=False,
                open=open_,
                ci_status="unknown",
                merge_status="pending" if open_ else "n/a",
                publish_status="n/a" if open_ else "pending",
                m1_deploy_status="n/a" if open_ else "pending",
                cf_deploy_status="n/a" if open_ else "pending",
                publish_sha=None,
            )
            self._runs[key] = row
        else:
            if title:
                row.title = title
            if head_sha:
                row.head_sha = head_sha
            if head_ref:
                row.head_ref = head_ref
            if html_url:
                row.html_url = html_url
            row.open = open_
            if open_:
                row.merge_status = "pending"
                row.publish_status = "n/a"
                row.m1_deploy_status = "n/a"
                row.cf_deploy_status = "n/a"
        self._touch(row)
        return row

    def apply_github_event(
        self,
        *,
        kind: str,
        payload: dict[str, object] | None,
        trace_id: str | None,
    ) -> None:
        if trace_id and not self.mark_processed(trace_id):
            return
        payload = payload or {}
        repo = str(payload.get("repository") or DEFAULT_REPO)
        action = str(payload.get("action") or "")

        if kind.startswith("pull_request."):
            self._apply_pull_request(repo, action, payload, kind)
        elif kind.startswith("check_run."):
            self._apply_check_run(repo, payload)
        elif kind.startswith("workflow_run."):
            self._apply_workflow_run(repo, payload)

    def _apply_pull_request(
        self, repo: str, action: str, payload: dict[str, object], kind: str
    ) -> None:
        pr = payload.get("pull_request")
        if not isinstance(pr, dict) or pr.get("number") is None:
            return
        pr_number = int(pr["number"])
        reviewed = _pr_has_reviewed_label(pr.get("labels"))
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
            row = self._upsert_pr(
                repo=repo,
                pr_number=pr_number,
                title=str(pr.get("title") or ""),
                head_sha=_str_or_none(pr.get("head_sha")),
                head_ref=_str_or_none(pr.get("head_ref")),
                html_url=_str_or_none(pr.get("html_url")),
                open_=False,
            )
            row.reviewed = reviewed or row.reviewed
            row.merge_status = "success" if merged else "skipped"
            if merged:
                row.publish_status = "pending"
                row.m1_deploy_status = "pending"
                row.cf_deploy_status = "pending"
            self._touch(row)
            return

        row = self._upsert_pr(
            repo=repo,
            pr_number=pr_number,
            title=str(pr.get("title") or ""),
            head_sha=_str_or_none(pr.get("head_sha")),
            head_ref=_str_or_none(pr.get("head_ref")),
            html_url=_str_or_none(pr.get("html_url")),
            open_=True,
        )
        row.reviewed = reviewed or row.reviewed
        if action == "synchronize" and row.head_sha:
            row.ci_status = "running"
        self._touch(row)

    def _apply_check_run(self, repo: str, payload: dict[str, object]) -> None:
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
        conclusion = _str_or_none(cr.get("conclusion"))
        for pr_number in pr_numbers:
            row = self._runs.get((repo, pr_number))
            if row is None:
                row = self._upsert_pr(repo=repo, pr_number=pr_number, open_=True)
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
            row.ci_status = _worst_ci(row.checks)
            self._touch(row)

    def _apply_workflow_run(self, repo: str, payload: dict[str, object]) -> None:
        wr = payload.get("workflow_run")
        if not isinstance(wr, dict):
            return
        name = str(wr.get("name") or "")
        if name != "publish":
            return
        conclusion = str(wr.get("conclusion") or "")
        head_sha = _str_or_none(wr.get("head_sha"))
        if conclusion == "success" and head_sha:
            self._last_publish_sha = head_sha
            if self._db is not None:
                self._db.set_last_publish_sha(head_sha)
        for row in self._runs.values():
            if not row.open and row.merge_status == "success":
                row.publish_status = "success" if conclusion == "success" else "failure"
                if head_sha:
                    row.publish_sha = head_sha
                self._touch(row)

    def apply_cloudflare_event(
        self,
        *,
        kind: str,
        payload: dict[str, object] | None,
        trace_id: str | None,
    ) -> None:
        if trace_id and not self.mark_processed(trace_id):
            return
        if not kind.startswith("pages.deployment."):
            return
        if "failure" in kind:
            status: PipelineStageStatus = "failure"
        elif "success" in kind:
            status = "success"
        else:
            status = "unknown"
        payload = payload or {}
        pages = payload.get("pages")
        pages_dict = pages if isinstance(pages, dict) else None
        target = resolve_pages_project(pages_dict)
        if target is None and pages_dict is not None:
            return
        if target is None:
            repo = DEFAULT_REPO
            expected_branch = "staging"
        else:
            repo = target["repo"]
            expected_branch = target["branch"]
        if not pages_branch_matches(pages_dict, expected_branch):
            return
        for row in self._runs.values():
            if row.repo != repo or row.open or row.merge_status != "success":
                continue
            row.cf_deploy_status = status
            self._touch(row)

    def apply_host_event(
        self,
        *,
        kind: str,
        payload: dict[str, object] | None,
        trace_id: str | None,
    ) -> None:
        if trace_id and not self.mark_processed(trace_id):
            return
        if not kind.endswith("converge.completed"):
            return
        now = _now_iso()
        if self._db is not None:
            self._db.set_meta("last_converge_at", now)
        for row in self._runs.values():
            if row.open or row.merge_status != "success":
                continue
            if row.publish_status != "success":
                continue
            if row.m1_deploy_status == "pending":
                row.m1_deploy_status = "success"
                self._touch(row)

    def apply_fleet_report(
        self,
        *,
        container_name: str,
        image_revision: str | None,
        report_status: str,
        age_s: float | None,
    ) -> None:
        if container_name not in M1_DEPLOY_QUORUM:
            return
        if not image_revision or report_status != "ok":
            return
        if age_s is not None and age_s >= 90:
            return
        target_sha = self._last_publish_sha
        if not target_sha:
            for row in self._runs.values():
                if row.publish_sha:
                    target_sha = row.publish_sha
                    break
        if not target_sha or image_revision != target_sha:
            return
        matched = 0
        for row in self._runs.values():
            if row.open or row.merge_status != "success":
                continue
            if row.publish_status != "success":
                continue
            if row.publish_sha and row.publish_sha != image_revision:
                continue
            matched += 1
            row.m1_deploy_status = "pending"
            self._touch(row)
        if matched:
            self._recompute_m1_deploy()

    def _recompute_m1_deploy(self) -> None:
        """Quorum check deferred to list time via fleet store — mark pending only here."""
        pass

    def recompute_m1_from_fleet(
        self,
        fleet_rows: list[tuple[str, str | None, str, float | None]],
    ) -> None:
        """Set m1_deploy from fleet quorum vs publish_sha on closed merged runs."""
        by_name = {name: (rev, status, age) for name, rev, status, age in fleet_rows}
        for row in self._runs.values():
            if row.open or row.merge_status != "success":
                continue
            if row.publish_status != "success" or not row.publish_sha:
                continue
            target = row.publish_sha
            ok = True
            for name in M1_DEPLOY_QUORUM:
                entry = by_name.get(name)
                if entry is None:
                    ok = False
                    break
                rev, status, age = entry
                if status != "ok" or not rev or rev != target:
                    ok = False
                    break
                if age is not None and age >= 90:
                    ok = False
                    break
            row.m1_deploy_status = "success" if ok else "pending"
            self._touch(row)


def _str_or_none(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _pr_has_reviewed_label(labels: object) -> bool:
    if not isinstance(labels, list):
        return False
    for item in labels:
        if item == "reviewed":
            return True
        if isinstance(item, dict) and item.get("name") == "reviewed":
            return True
    return False