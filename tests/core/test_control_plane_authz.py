"""Unit tests for control-plane authorize + job filter (ADR-103 Blocks 4–7)."""

from __future__ import annotations

from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_authz import (
    ResourceRef,
    authorize,
    can_mutate_agents,
    filter_jobs_visible,
)


def _principal(
    *,
    user_id: str = "rx:user:a",
    admin: bool = False,
    orgs: frozenset[str] | None = None,
) -> ControlPlanePrincipal:
    roles = frozenset(
        {GlobalRole.ADMIN.value if admin else GlobalRole.MEMBER.value}
    )
    return ControlPlanePrincipal(
        user_id=user_id,
        roles=roles,
        org_ids=orgs or frozenset(),
        active_org_id=None,
        via="session",
    )


class TestAuthorize:
    def test_admin_sees_all(self) -> None:
        p = _principal(admin=True)
        d = authorize(
            p,
            "jobs.read",
            ResourceRef(kind="job", owner_user_id="rx:user:other"),
        )
        assert d.allowed

    def test_owner_private(self) -> None:
        p = _principal(user_id="rx:user:a")
        d = authorize(
            p,
            "jobs.read",
            ResourceRef(kind="job", owner_user_id="rx:user:a"),
        )
        assert d.allowed

    def test_outsider_denied(self) -> None:
        p = _principal(user_id="rx:user:a")
        d = authorize(
            p,
            "jobs.read",
            ResourceRef(kind="job", owner_user_id="rx:user:b"),
        )
        assert not d.allowed

    def test_org_peer(self) -> None:
        p = _principal(user_id="rx:user:a", orgs=frozenset({"org:1"}))
        d = authorize(
            p,
            "jobs.steer",
            ResourceRef(
                kind="job", owner_user_id="rx:user:b", org_id="org:1"
            ),
        )
        assert d.allowed

    def test_member_cannot_mutate_agents(self) -> None:
        p = _principal()
        assert not can_mutate_agents(p)
        d = authorize(p, "agents.soul.write", ResourceRef(kind="agent"))
        assert not d.allowed

    def test_member_may_create_org(self) -> None:
        p = _principal()
        assert authorize(p, "orgs.create").allowed


class TestFilterJobs:
    def test_member_sees_own_and_org_only(self) -> None:
        p = _principal(user_id="rx:user:a", orgs=frozenset({"org:1"}))
        jobs = [
            {"job_id": "j1"},
            {"job_id": "j2"},
            {"job_id": "j3"},
            {"job_id": "j4"},
        ]
        meta: dict[str, tuple[str | None, str | None]] = {
            "j1": ("rx:user:a", None),
            "j2": ("rx:user:b", "org:1"),
            "j3": ("rx:user:c", None),
            "j4": (None, None),
        }
        visible = filter_jobs_visible(p, jobs, meta_by_job=meta)
        ids = {j["job_id"] for j in visible}
        assert ids == {"j1", "j2"}
