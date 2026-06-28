"""Trim webhook payloads before publishing on the event bus."""

from __future__ import annotations

from typing import Any


def summarize_github_payload(payload: dict[str, Any]) -> dict[str, Any]:
    repo_node = payload.get("repository")
    repo = repo_node if isinstance(repo_node, dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    return {
        "action": payload.get("action"),
        "repository": repo.get("full_name"),
        "sender": sender.get("login"),
        "check_run": _id_name(payload.get("check_run")),
        "workflow_run": _id_name(payload.get("workflow_run")),
        "pull_request": _id_name(payload.get("pull_request")),
    }


def summarize_cloudflare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": payload.get("name"),
        "text": payload.get("text"),
        "alert_type": payload.get("alert_type"),
        "account_name": payload.get("account_name"),
        "policy_name": payload.get("policy_name"),
    }


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