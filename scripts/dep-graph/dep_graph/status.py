"""Status derivation and blocker detection for dep-graph cards."""

from __future__ import annotations

from .keys import format_key


def _has_active_blockers(
    gh_entry: dict,
    extra_blocked_by: list[tuple[str, int]],
    gh_issues: dict,
    own_repo: str,
) -> bool:
    """Return True if any blocker is still open."""
    for item in gh_entry.get("blocked_by", []):
        if isinstance(item, dict):
            key = format_key(item["repo"], item["issue"])
        else:
            key = format_key(own_repo, item) if own_repo else str(item)
        if gh_issues.get(key, {}).get("state") != "closed":
            return True
    for dep_repo, dep_num in extra_blocked_by:
        key = format_key(dep_repo, dep_num)
        if gh_issues.get(key, {}).get("state") != "closed":
            return True
    return False


def derive_status(
    ovr: dict,
    gh_entry: dict | None,
    extra_blocked_by: list[tuple[str, int]],
    gh_issues: dict,
    repo: str = "",
) -> str:
    if "status" in ovr:
        return ovr["status"]
    if gh_entry is None:
        return "ready"
    if gh_entry.get("defer"):
        return "defer"
    if gh_entry.get("state") == "closed":
        return "done"
    if _has_active_blockers(gh_entry, extra_blocked_by, gh_issues, repo):
        return "blocked"
    return "ready"
