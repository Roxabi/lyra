"""PipelineStore unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.nats.pipeline_store import M1_DEPLOY_QUORUM, PipelineStore


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