"""UserStore profile create/update for dashboard admin."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.infrastructure.stores.identity.user_store import UserStore


@pytest.mark.asyncio
async def test_create_and_update_profile_user(tmp_path: Path) -> None:
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        user = await store.create_profile_user(
            display_name="Jane Doe",
            email="Jane@Example.COM",
        )
        assert user.id.startswith("rx:user:")
        assert user.display_name == "Jane Doe"
        assert user.email == "jane@example.com"

        updated = await store.update_profile_user(
            user.id,
            display_name="Jane D.",
            email="jane.d@example.com",
        )
        assert updated is not None
        assert updated.display_name == "Jane D."
        assert updated.email == "jane.d@example.com"

        listed = await store.list_users()
        assert any(row.id == user.id for row in listed)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_profile_user_rejects_duplicate_email(tmp_path: Path) -> None:
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        await store.create_profile_user(
            display_name="First",
            email="dup@example.com",
        )
        with pytest.raises(ValueError, match="email already registered"):
            await store.create_profile_user(
                display_name="Second",
                email="dup@example.com",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_profile_user_removes_user_and_identities(tmp_path: Path) -> None:
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        user = await store.create_profile_user(
            display_name="Ops",
            email="ops@example.com",
        )
        await store.set_platform_identity(user.id, "telegram", "12345")
        assert await store.delete_profile_user(user.id) is True
        assert await store.get_user(user.id) is None
        assert await store.list_platform_identities(user.id) == ()
        assert store.resolve_user_id("tg:user:12345") is None
        assert await store.delete_profile_user(user.id) is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_platform_identity_rejects_cross_user_collision(
    tmp_path: Path,
) -> None:
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        first = await store.create_profile_user(
            display_name="First",
            email="first@example.com",
        )
        second = await store.create_profile_user(
            display_name="Second",
            email="second@example.com",
        )
        await store.set_platform_identity(first.id, "telegram", "12345")
        with pytest.raises(ValueError, match="platform identity already linked"):
            await store.set_platform_identity(second.id, "telegram", "12345")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_set_platform_identity_links_and_clears(tmp_path: Path) -> None:
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        user = await store.create_profile_user(
            display_name="Ops",
            email="ops@example.com",
        )
        await store.set_platform_identity(user.id, "telegram", "12345")
        identities = await store.list_platform_identities(user.id)
        assert len(identities) == 1
        assert identities[0].platform_uid == "12345"
        assert store.resolve_user_id("tg:user:12345") == user.id

        await store.set_platform_identity(user.id, "telegram", "67890")
        identities = await store.list_platform_identities(user.id)
        assert len(identities) == 1
        assert identities[0].platform_uid == "67890"

        await store.set_platform_identity(user.id, "telegram", None)
        identities = await store.list_platform_identities(user.id)
        assert identities == ()
    finally:
        await store.close()