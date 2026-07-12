"""Control-plane resource authorization (ADR-103 Blocks 4–7). Pure helpers."""

from __future__ import annotations

from dataclasses import dataclass

from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole

__all__ = [
    "AuthzAction",
    "AuthzDecision",
    "ResourceRef",
    "authorize",
    "can_mutate_agents",
    "filter_jobs_visible",
]

# V1: member may not mutate agents/soul (open item #3 locked).
_ADMIN_ONLY_ACTIONS = frozenset(
    {
        "agents.write",
        "agents.soul.write",
        "users.invite",
        "users.admin",
    }
)


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """A control-plane resource for visibility checks."""

    kind: str
    owner_user_id: str | None = None
    org_id: str | None = None
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthzDecision:
    allowed: bool
    reason: str = ""


# Free-form action strings used by handlers (not an Enum — open for extension).
AuthzAction = str


def can_mutate_agents(principal: ControlPlanePrincipal) -> bool:
    return principal.is_admin


def authorize(
    principal: ControlPlanePrincipal,
    action: AuthzAction,
    resource: ResourceRef | None = None,
) -> AuthzDecision:
    """Return whether *principal* may perform *action* on *resource*."""
    if GlobalRole.ADMIN.value in principal.roles:
        return AuthzDecision(True, "admin")

    if action in _ADMIN_ONLY_ACTIONS:
        return AuthzDecision(False, "admin_only")

    if action in {"orgs.create"}:
        # Open item #2: any active member may create orgs
        return AuthzDecision(True, "member_create_org")

    if resource is None:
        return AuthzDecision(False, "no_resource")

    if resource.owner_user_id and resource.owner_user_id == principal.user_id:
        return AuthzDecision(True, "owner")

    if resource.org_id and resource.org_id in principal.org_ids:
        return AuthzDecision(True, "org_member")

    return AuthzDecision(False, "denied")


def filter_jobs_visible(
    principal: ControlPlanePrincipal,
    jobs: list[dict],
    *,
    meta_by_job: dict[str, tuple[str | None, str | None]],
) -> list[dict]:
    """Filter job row dicts by ownership / org / admin.

    *meta_by_job* maps job_id → (launched_by, org_id).
    Jobs without meta are visible only to admin (unknown owner).
    """
    if principal.is_admin:
        return jobs
    visible: list[dict] = []
    for row in jobs:
        jid = str(row.get("job_id") or "")
        launched_by, org_id = meta_by_job.get(jid, (None, None))
        decision = authorize(
            principal,
            "jobs.read",
            ResourceRef(kind="job", owner_user_id=launched_by, org_id=org_id),
        )
        if decision.allowed:
            visible.append(row)
    return visible
