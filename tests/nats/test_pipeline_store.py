"""PipelineStore unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.nats.pipeline import M1_DEPLOY_QUORUM, PipelineStore


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(db_path=tmp_path / "pipeline.db")


def _open_pr_payload(
    *,
    pr_number: int = 42,
    labels: list[str] | None = None,
    head_sha: str = "abc123",
) -> dict[str, object]:
    pr: dict[str, object] = {
        "number": pr_number,
        "title": f"PR #{pr_number}",
        "head_sha": head_sha,
        "head_ref": "feat/test",
        "html_url": f"https://github.com/Roxabi/roxabi-factory/pull/{pr_number}",
    }
    if labels:
        pr["labels"] = labels
    return {"pull_request": pr}


def test_pull_request_open_and_reviewed_label(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(labels=["reviewed"]),
        trace_id="t1",
    )
    row = store.get_run("Roxabi/roxabi-factory", 42)
    assert row is not None
    assert row.open is True
    assert row.reviewed is True
    assert row.merge_status == "pending"


def test_labeled_event_sets_reviewed(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(pr_number=7),
        trace_id="t-open",
    )
    store.apply_github_event(
        kind="pull_request.labeled",
        payload={
            "pull_request": _open_pr_payload(pr_number=7)["pull_request"],
            "label": {"name": "reviewed"},
        },
        trace_id="t-label",
    )
    row = store.get_run("Roxabi/roxabi-factory", 7)
    assert row is not None
    assert row.reviewed is True


def test_check_run_updates_ci_status(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(pr_number=9),
        trace_id="t-open",
    )
    store.apply_github_event(
        kind="check_run.completed",
        payload={
            "check_run": {
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "pull_request_numbers": [9],
            }
        },
        trace_id="t-check",
    )
    row = store.get_run("Roxabi/roxabi-factory", 9)
    assert row is not None
    assert row.ci_status == "success"
    assert len(row.checks) == 1


def test_merged_pr_publish_and_m1_quorum(store: PipelineStore) -> None:
    publish_sha = "deadbeef" * 5
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {
                "number": 99,
                "title": "merged",
                "merged": True,
                "head_sha": "head99",
                "html_url": "https://github.com/Roxabi/roxabi-factory/pull/99",
            }
        },
        trace_id="t-close",
    )
    store.apply_github_event(
        kind="workflow_run.completed",
        payload={
            "workflow_run": {
                "name": "publish",
                "conclusion": "success",
                "head_sha": publish_sha,
            }
        },
        trace_id="t-publish",
    )
    fleet_rows = [
        (name, publish_sha, "ok", 12.0) for name in M1_DEPLOY_QUORUM
    ]
    store.recompute_m1_from_fleet(fleet_rows)
    row = store.get_run("Roxabi/roxabi-factory", 99)
    assert row is not None
    assert row.merge_status == "success"
    assert row.publish_status == "success"
    assert row.m1_deploy_status == "success"


def test_sqlite_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    first = PipelineStore(db_path=db)
    first.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(pr_number=55, labels=["reviewed"]),
        trace_id="persist-1",
    )
    second = PipelineStore(db_path=db)
    row = second.get_run("Roxabi/roxabi-factory", 55)
    assert row is not None
    assert row.reviewed is True


def test_converge_completed_does_not_set_m1_without_fleet(store: PipelineStore) -> None:
    publish_sha = "sha" * 10
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {
                "number": 12,
                "title": "ship",
                "merged": True,
                "head_sha": "head12",
            },
        },
        trace_id="t-merge",
    )
    store.apply_github_event(
        kind="workflow_run.completed",
        payload={
            "workflow_run": {
                "name": "publish",
                "conclusion": "success",
                "head_sha": publish_sha,
            }
        },
        trace_id="t-pub",
    )
    store.apply_host_event(
        kind="roxabituwer.converge.completed",
        payload={"drift_kind": "none"},
        trace_id="t-converge",
    )
    row = store.get_run("Roxabi/roxabi-factory", 12)
    assert row is not None
    assert row.m1_deploy_status == "pending"


def test_publish_only_updates_active_pending_merge(store: PipelineStore) -> None:
    first_sha = "a" * 40
    second_sha = "b" * 40
    for pr_number, trace in ((80, "t-merge-80"), (81, "t-merge-81")):
        store.apply_github_event(
            kind="pull_request.closed",
            payload={
                "action": "closed",
                "pull_request": {
                    "number": pr_number,
                    "title": f"PR {pr_number}",
                    "merged": True,
                },
            },
            trace_id=trace,
        )
    store.apply_github_event(
        kind="workflow_run.completed",
        payload={
            "workflow_run": {
                "name": "publish",
                "conclusion": "success",
                "head_sha": first_sha,
            }
        },
        trace_id="t-publish-1",
    )
    row80 = store.get_run("Roxabi/roxabi-factory", 80)
    row81 = store.get_run("Roxabi/roxabi-factory", 81)
    assert row80 is not None and row81 is not None
    assert row80.publish_status == "pending"
    assert row81.publish_status == "success"
    assert row81.publish_sha == first_sha

    store.apply_github_event(
        kind="workflow_run.completed",
        payload={
            "workflow_run": {
                "name": "publish",
                "conclusion": "success",
                "head_sha": second_sha,
            }
        },
        trace_id="t-publish-2",
    )
    row80 = store.get_run("Roxabi/roxabi-factory", 80)
    row81 = store.get_run("Roxabi/roxabi-factory", 81)
    assert row80 is not None and row81 is not None
    assert row80.publish_status == "success"
    assert row80.publish_sha == second_sha
    assert row81.publish_sha == first_sha


def test_cf_requires_structured_pages_payload(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {"number": 36, "title": "x", "merged": True},
        },
        trace_id="t-merge-no-pages",
    )
    store.apply_cloudflare_event(
        kind="pages.deployment.success",
        payload={"text": "deployment succeeded"},
        trace_id="t-cf-no-pages",
    )
    row = store.get_run("Roxabi/roxabi-factory", 36)
    assert row is not None
    assert row.cf_deploy_status == "pending"


def test_cf_pages_success_updates_cf_deploy(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {
                "number": 33,
                "title": "pages",
                "merged": True,
            },
        },
        trace_id="t-merge-pages",
    )
    store.apply_cloudflare_event(
        kind="pages.deployment.success",
        payload={
            "pages": {
                "project_name": "roxabi-factory-dashboard",
                "branch": "staging",
                "deployment_status": "success",
            }
        },
        trace_id="t-cf",
    )
    row = store.get_run("Roxabi/roxabi-factory", 33)
    assert row is not None
    assert row.cf_deploy_status == "success"


def test_cf_pages_ignores_unknown_project(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {"number": 34, "title": "x", "merged": True},
        },
        trace_id="t-merge-cf",
    )
    store.apply_cloudflare_event(
        kind="pages.deployment.success",
        payload={"pages": {"project_name": "unknown-project", "branch": "staging"}},
        trace_id="t-cf-unknown",
    )
    row = store.get_run("Roxabi/roxabi-factory", 34)
    assert row is not None
    assert row.cf_deploy_status == "pending"


def test_cf_pages_ignores_branch_mismatch(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {"number": 35, "title": "x", "merged": True},
        },
        trace_id="t-merge-branch",
    )
    store.apply_cloudflare_event(
        kind="pages.deployment.success",
        payload={
            "pages": {
                "project_name": "roxabi-factory",
                "branch": "main",
                "deployment_status": "success",
            }
        },
        trace_id="t-cf-branch",
    )
    row = store.get_run("Roxabi/roxabi-factory", 35)
    assert row is not None
    assert row.cf_deploy_status == "pending"


def test_closed_unmerged_pr_deploy_stages_are_na(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {
                "number": 2,
                "title": "closed not merged",
                "merged": False,
            },
        },
        trace_id="t-close-skip",
    )
    row = store.get_run("Roxabi/roxabi-factory", 2)
    assert row is not None
    assert row.merge_status == "skipped"
    assert row.publish_status == "n/a"
    assert row.m1_deploy_status == "n/a"
    assert row.cf_deploy_status == "n/a"


def test_list_runs_filters_stale_closed_rows(store: PipelineStore) -> None:
    store.apply_github_event(
        kind="pull_request.closed",
        payload={
            "action": "closed",
            "pull_request": {"number": 3, "title": "old", "merged": True},
        },
        trace_id="t-old-merge",
    )
    row = store.get_run("Roxabi/roxabi-factory", 3)
    assert row is not None
    row.last_event_at = "2020-01-01T00:00:00+00:00"
    row.updated_at = row.last_event_at
    store.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(pr_number=4),
        trace_id="t-open-new",
    )
    listed = store.list_runs(include_closed_hours=24.0)
    numbers = {r.pr_number for r in listed}
    assert 4 in numbers
    assert 3 not in numbers


def test_idempotent_trace_id(store: PipelineStore) -> None:
    payload = _open_pr_payload(pr_number=1, labels=["reviewed"])
    store.apply_github_event(
        kind="pull_request.opened",
        payload=payload,
        trace_id="dup",
    )
    store.apply_github_event(
        kind="pull_request.opened",
        payload=_open_pr_payload(pr_number=1, labels=[]),
        trace_id="dup",
    )
    row = store.get_run("Roxabi/roxabi-factory", 1)
    assert row is not None
    assert row.reviewed is True