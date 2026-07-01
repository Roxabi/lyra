"""Ingress payload summarization tests."""

from __future__ import annotations

from factory.ingress.payload import summarize_github_payload


def test_pull_request_summary_includes_labels_and_head() -> None:
    summary = summarize_github_payload(
        {
            "action": "opened",
            "repository": {"full_name": "Roxabi/roxabi-factory"},
            "pull_request": {
                "number": 12,
                "title": "feat",
                "labels": [{"name": "reviewed"}],
                "head": {"sha": "sha12", "ref": "feat/x"},
                "html_url": "https://github.com/Roxabi/roxabi-factory/pull/12",
            },
        }
    )
    pr = summary["pull_request"]
    assert isinstance(pr, dict)
    assert pr["labels"] == ["reviewed"]
    assert pr["head_sha"] == "sha12"
    assert pr["head_ref"] == "feat/x"


def test_labeled_event_includes_root_label() -> None:
    summary = summarize_github_payload(
        {
            "action": "labeled",
            "repository": {"full_name": "Roxabi/roxabi-factory"},
            "label": {"name": "reviewed"},
            "pull_request": {
                "number": 3,
                "title": "labeled pr",
                "labels": [],
            },
        }
    )
    assert summary["label"] == {"name": "reviewed"}
    pr = summary["pull_request"]
    assert isinstance(pr, dict)
    assert pr["labels"] == ["reviewed"]


def test_check_run_includes_pr_numbers() -> None:
    summary = summarize_github_payload(
        {
            "action": "completed",
            "repository": {"full_name": "Roxabi/roxabi-factory"},
            "check_run": {
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "pull_requests": [{"number": 5}, {"number": 6}],
            },
        }
    )
    cr = summary["check_run"]
    assert isinstance(cr, dict)
    assert cr["pull_request_numbers"] == [5, 6]


def test_workflow_run_includes_head_sha() -> None:
    summary = summarize_github_payload(
        {
            "action": "completed",
            "repository": {"full_name": "Roxabi/roxabi-factory"},
            "workflow_run": {
                "name": "publish",
                "conclusion": "success",
                "head_sha": "abc",
                "status": "completed",
            },
        }
    )
    wr = summary["workflow_run"]
    assert isinstance(wr, dict)
    assert wr["head_sha"] == "abc"
    assert wr["name"] == "publish"