"""Control-plane identity store — users, invites, sessions, API keys (ADR-103)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.core.auth.control_plane import GlobalRole, InviteStatus, UserStatus
from factory.core.auth.platform_keys import USER_ID_PREFIX
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore
from factory.infrastructure.stores.identity.password_hash import (
    hash_password,
    verify_password,
)


@pytest.fixture
async def store(tmp_path: Path):
    s = ControlPlaneStore(tmp_path / "auth.db")
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


class TestPasswordHash:
    def test_roundtrip(self) -> None:
        encoded = hash_password("s3cret-pass")
        assert verify_password("s3cret-pass", encoded)
        assert not verify_password("wrong", encoded)


class TestUsers:
    @pytest.mark.asyncio
    async def test_create_and_verify(self, store: ControlPlaneStore) -> None:
        user = await store.create_user(
            email="Admin@Example.com",
            password="hunter2!!",
            global_role=GlobalRole.ADMIN,
        )
        assert user.id.startswith(USER_ID_PREFIX)
        assert user.email == "admin@example.com"
        assert user.global_role is GlobalRole.ADMIN
        assert user.status is UserStatus.ACTIVE
        assert user.has_password

        ok = await store.verify_password("admin@example.com", "hunter2!!")
        assert ok is not None and ok.id == user.id
        assert await store.verify_password("admin@example.com", "nope") is None

    @pytest.mark.asyncio
    async def test_duplicate_email(self, store: ControlPlaneStore) -> None:
        await store.create_user(email="a@x.com", password="password1")
        with pytest.raises(ValueError, match="already registered"):
            await store.create_user(email="a@x.com", password="password2")

    @pytest.mark.asyncio
    async def test_bootstrap_admin_idempotent(self, store: ControlPlaneStore) -> None:
        first = await store.bootstrap_admin_if_empty(
            email="root@local",
            password="bootstrap-secret",
        )
        assert first is not None
        assert first.global_role is GlobalRole.ADMIN
        second = await store.bootstrap_admin_if_empty(
            email="other@local",
            password="other-secret",
        )
        assert second is None


class TestInvites:
    @pytest.mark.asyncio
    async def test_invite_lifecycle(self, store: ControlPlaneStore) -> None:
        admin = await store.create_user(
            email="admin@x.com",
            password="password1",
            global_role=GlobalRole.ADMIN,
        )
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        rec, token = await store.create_invite(
            email="new@x.com",
            invited_by=admin.id,
            expires_at=expires,
        )
        assert rec.status is InviteStatus.PENDING
        assert token

        member = await store.accept_invite(
            raw_token=token,
            password="member-pass",
            display_name="New",
        )
        assert member.email == "new@x.com"
        assert member.global_role is GlobalRole.MEMBER

        with pytest.raises(ValueError, match="not pending"):
            await store.accept_invite(raw_token=token, password="again-pass")

    @pytest.mark.asyncio
    async def test_invite_only_no_open_signup(self, store: ControlPlaneStore) -> None:
        """No register() — only create_user (admin/bootstrap) or accept_invite."""
        assert not hasattr(store, "register")

    @pytest.mark.asyncio
    async def test_revoke_invite(self, store: ControlPlaneStore) -> None:
        admin = await store.create_user(
            email="a@x.com", password="password1", global_role=GlobalRole.ADMIN
        )
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        rec, token = await store.create_invite(
            email="b@x.com", invited_by=admin.id, expires_at=expires
        )
        assert await store.revoke_invite(rec.id)
        with pytest.raises(ValueError):
            await store.accept_invite(raw_token=token, password="password1")


class TestSessionsAndApiKeys:
    @pytest.mark.asyncio
    async def test_session_roundtrip(self, store: ControlPlaneStore) -> None:
        user = await store.create_user(email="u@x.com", password="password1")
        _rec, token = await store.create_session(user.id)
        principal = await store.resolve_session(token)
        assert principal is not None
        assert principal.user_id == user.id
        assert principal.via == "session"
        assert GlobalRole.MEMBER.value in principal.roles
        assert await store.revoke_session(token)
        assert await store.resolve_session(token) is None

    @pytest.mark.asyncio
    async def test_api_key_roundtrip(self, store: ControlPlaneStore) -> None:
        user = await store.create_user(email="k@x.com", password="password1")
        rec, secret = await store.create_api_key(user.id, name="ci")
        assert secret.startswith("fak_")
        assert rec.prefix == secret[:12]
        principal = await store.resolve_api_key(secret)
        assert principal is not None
        assert principal.via == "api_key"
        assert await store.revoke_api_key(rec.id, user_id=user.id)
        assert await store.resolve_api_key(secret) is None
