"""In-memory PR pipeline read model (plane ④) for dashboard /pipeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory.nats.pipeline_cf_registry import pages_branch_matches, resolve_pages_project
from factory.nats.pipeline_correlation import active_cf_pending_row
from factory.nats.pipeline_db import PipelineDb, default_db_path
from factory.nats.pipeline_github_apply import (
    apply_check_run,
    apply_pull_request,
    apply_workflow_run,
    github_repo,
)
from factory.nats.pipeline_models import (
    DEFAULT_REPO,
    M1_DEPLOY_QUORUM,
    PipelineCheckRow,
    PipelineRunRow,
    PipelineStageStatus,
    now_iso,
    parse_iso,
)

__all__ = [
    "DEFAULT_REPO",
    "M1_DEPLOY_QUORUM",
    "PipelineCheckRow",
    "PipelineRunRow",
    "PipelineStageStatus",
    "PipelineStore",
]


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
            self._db.record_processed(event_id, now_iso())
        elif len(self._processed) > 10_000:
            self._processed.clear()
        return True

    def list_runs(self, *, include_closed_hours: float = 24.0) -> list[PipelineRunRow]:
        cutoff = None
        if include_closed_hours > 0:
            cutoff = datetime.now(tz=UTC) - timedelta(hours=include_closed_hours)
        rows: list[PipelineRunRow] = []
        for row in self._runs.values():
            if row.open:
                rows.append(row)
                continue
            if cutoff is None:
                rows.append(row)
                continue
            ts = parse_iso(row.last_event_at) or parse_iso(row.updated_at)
            if ts is None or ts >= cutoff:
                rows.append(row)
        rows.sort(
            key=lambda r: (not r.open, r.pr_number),
            reverse=True,
        )
        return rows

    def get_run(self, repo: str, pr_number: int) -> PipelineRunRow | None:
        return self._runs.get((repo, pr_number))

    def _touch(self, row: PipelineRunRow) -> None:
        now = now_iso()
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
        repo = github_repo(payload)
        action = str(payload.get("action") or "")

        if kind.startswith("pull_request."):
            apply_pull_request(self, repo, action, payload, kind)
        elif kind.startswith("check_run."):
            apply_check_run(self, repo, payload)
        elif kind.startswith("workflow_run."):
            apply_workflow_run(self, repo, payload)

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
        if pages_dict is None:
            return
        target = resolve_pages_project(pages_dict)
        if target is None:
            return
        repo = target["repo"]
        expected_branch = target["branch"]
        if not pages_branch_matches(pages_dict, expected_branch):
            return
        row = active_cf_pending_row(self._runs.values(), repo)
        if row is None:
            return
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
        if self._db is not None:
            self._db.set_meta("last_converge_at", now_iso())

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