"""CF Pages project → repo correlation for pipeline cf_deploy stage."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_REPO = "Roxabi/roxabi-factory"

# Override via PIPELINE_CF_PAGES_PROJECTS_JSON env if needed.
_DEFAULT_PROJECTS: dict[str, dict[str, str]] = {
    "roxabi-factory": {"repo": DEFAULT_REPO, "branch": "staging"},
    "roxabi-factory-dashboard": {"repo": DEFAULT_REPO, "branch": "staging"},
}


def _load_projects() -> dict[str, dict[str, str]]:
    raw = os.environ.get("PIPELINE_CF_PAGES_PROJECTS_JSON", "").strip()
    if not raw:
        return dict(_DEFAULT_PROJECTS)
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return dict(_DEFAULT_PROJECTS)
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def resolve_pages_project(pages: dict[str, Any] | None) -> dict[str, str] | None:
    """Map structured Pages payload to a registry entry."""
    if not pages:
        return None
    project = pages.get("project_name") or pages.get("project_id")
    if not isinstance(project, str) or not project.strip():
        return None
    entry = _load_projects().get(project.strip())
    if entry is None:
        return None
    repo = entry.get("repo")
    if not isinstance(repo, str) or not repo:
        return None
    branch = entry.get("branch")
    return {
        "repo": repo,
        "branch": str(branch) if isinstance(branch, str) and branch else "staging",
    }


def resolve_pages_repo(pages: dict[str, Any] | None) -> str | None:
    """Map structured Pages payload to a GitHub repo full_name."""
    entry = resolve_pages_project(pages)
    return entry["repo"] if entry else None


def pages_branch_matches(pages: dict[str, Any] | None, expected_branch: str) -> bool:
    if not pages:
        return True
    branch = pages.get("branch") or pages.get("environment")
    if not isinstance(branch, str) or not branch.strip():
        return True
    return branch.strip() == expected_branch