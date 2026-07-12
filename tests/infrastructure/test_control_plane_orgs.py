"""Control-plane org store + principal org_ids (ADR-103 Block 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.auth.control_plane import GlobalRole
from factory.core.auth.control_plane_org import OrgRole
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore


@pytest.fixture
async def store(tmp_path: Path):
    s = ControlPlaneStore(tmp_path / "auth.db")
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_create_org_and_membership(store: ControlPlaneStore) -> None:
    owner = await store.create_user(
        email="o@x.com", password="password1", global_role=GlobalRole.ADMIN
    )
    member = await store.create_user(email="m@x.com", password="password1")
    org = await store.create_org(name="Team", created_by=owner.id)
    assert org.id.startswith("org:")
    await store.add_org_member(
        org.id,
        member.id,
        org_role=OrgRole.MEMBER,
        actor_user_id=owner.id,
        actor_is_admin=True,
    )
    orgs = await store.list_orgs_for_user(member.id)
    assert len(orgs) == 1 and orgs[0].id == org.id
    principal = await store.principal_for_user(member, via="session")
    assert org.id in principal.org_ids


@pytest.mark.asyncio
async def test_member_cannot_add_without_owner(store: ControlPlaneStore) -> None:
    owner = await store.create_user(email="o@x.com", password="password1")
    peer = await store.create_user(email="p@x.com", password="password1")
    other = await store.create_user(email="z@x.com", password="password1")
    org = await store.create_org(name="T", created_by=owner.id)
    await store.add_org_member(
        org.id, peer.id, actor_user_id=owner.id, actor_is_admin=False
    )
    with pytest.raises(PermissionError):
        await store.add_org_member(
            org.id, other.id, actor_user_id=peer.id, actor_is_admin=False
        )


@pytest.mark.asyncio
async def test_job_meta_roundtrip(store: ControlPlaneStore) -> None:
    await store.record_job_launch("job-1", launched_by="rx:user:a", org_id="org:1")
    by, org = await store.get_job_meta("job-1")
    assert by == "rx:user:a" and org == "org:1"
    m = await store.job_meta_map(["job-1", "missing"])
    assert m["job-1"] == ("rx:user:a", "org:1")
