"""Trim webhook payloads before publishing on the event bus."""

from __future__ import annotations

from typing import Any


def summarize_github_payload(payload: dict[str, Any]) -> dict[str, Any]:
    repo_node = payload.get("repository")
    repo = repo_node if isinstance(repo_node, dict) else {}
    sender_node = payload.get("sender")
    sender = sender_node if isinstance(sender_node, dict) else {}
    action = payload.get("action")
    label_node = payload.get("label")
    out: dict[str, Any] = {
        "action": action,
        "repository": repo.get("full_name"),
        "sender": sender.get("login"),
        "check_run": _check_run_summary(payload.get("check_run")),
        "workflow_run": _workflow_run_summary(payload.get("workflow_run")),
        "pull_request": _pull_request_summary(
            payload.get("pull_request"),
            event_label=label_node if action in {"labeled", "unlabeled"} else None,
        ),
    }
    if action in {"labeled", "unlabeled"} and isinstance(label_node, dict):
        out["label"] = label_node
    return out


def summarize_cloudflare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    data_dict = data if isinstance(data, dict) else {}
    out: dict[str, Any] = {
        "name": payload.get("name"),
        "text": payload.get("text"),
        "alert_type": payload.get("alert_type"),
        "account_name": payload.get("account_name"),
        "policy_name": payload.get("policy_name"),
        "account_id": payload.get("account_id"),
        "data": data_dict,
    }
    pages = _pages_deployment_summary(data_dict, payload)
    if pages:
        out["pages"] = pages
    return out


def _pages_deployment_summary(
    data: dict[str, Any],
    root: dict[str, Any],
) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for key in (
        "project_name",
        "project_id",
        "deployment_id",
        "environment",
        "url",
        "branch",
        "deployment_status",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    for key in ("project_name", "project_id"):
        value = root.get(key)
        if isinstance(value, str) and value.strip() and key not in out:
            out[key] = value.strip()
    return out or None


def _pr_label_names(labels_raw: object, event_label: Any) -> list[str]:
    labels: list[str] = []
    if isinstance(labels_raw, list):
        for item in labels_raw:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    labels.append(name)
    event_label_dict = event_label if isinstance(event_label, dict) else None
    label_name = payload_label_name(event_label_dict)
    if label_name and label_name not in labels:
        labels.append(label_name)
    return labels


def _pull_request_summary(
    node: Any,
    *,
    event_label: Any = None,
) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    head = node.get("head")
    head_dict = head if isinstance(head, dict) else {}
    labels = _pr_label_names(node.get("labels"), event_label)
    out: dict[str, Any] = {}
    for key in ("number", "title", "state", "merged", "html_url"):
        if node.get(key) is not None:
            out[key] = node.get(key)
    if head_dict.get("sha"):
        out["head_sha"] = head_dict.get("sha")
    if head_dict.get("ref"):
        out["head_ref"] = head_dict.get("ref")
    if labels:
        out["labels"] = labels
    return out or None


def payload_label_name(label: dict[str, Any] | None) -> str | None:
    """Extract label name from a GitHub label object."""
    if isinstance(label, dict) and isinstance(label.get("name"), str):
        return label.get("name")
    return None


def _check_run_summary(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    out = _id_name(node) or {}
    head = node.get("head")
    if isinstance(head, dict) and head.get("sha"):
        out["head_sha"] = head.get("sha")
    prs = node.get("pull_requests")
    if isinstance(prs, list):
        numbers = [
            pr.get("number")
            for pr in prs
            if isinstance(pr, dict) and pr.get("number") is not None
        ]
        if numbers:
            out["pull_request_numbers"] = numbers
    return out or None


def _workflow_run_summary(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    out = _id_name(node) or {}
    for key in ("event", "head_branch", "head_sha", "conclusion", "status"):
        if isinstance(node.get(key), str):
            out[key] = node.get(key)
    return out or None


def _id_name(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    out: dict[str, Any] = {}
    if node.get("id") is not None:
        out["id"] = node.get("id")
    for key in ("name", "conclusion", "status"):
        if isinstance(node.get(key), str):
            out[key] = node.get(key)
    return out or None