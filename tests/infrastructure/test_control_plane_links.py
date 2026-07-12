"""Platform link codes + chat_ready (ADR-103 Blocks 8–9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.auth.control_plane import GlobalRole
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
async def test_dual_link_chat_ready(store: ControlPlaneStore) -> None:
    user = await store.create_user(
        email="u@x.com", password="password1", global_role=GlobalRole.MEMBER
    )
    assert await store.chat_ready(user.id) is False

    _id, token_tg = await store.create_link_code(user.id, platform="telegram")
    await store.consume_link_code(token_tg, platform_key="tg:user:111")
    assert await store.chat_ready(user.id) is False
    assert await store.is_platform_chat_ready("tg:user:111") is False

    _id2, token_dc = await store.create_link_code(user.id, platform="discord")
    await store.consume_link_code(token_dc, platform_key="dc:user:222")
    assert await store.chat_ready(user.id) is True
    assert await store.is_platform_chat_ready("tg:user:111") is True
    assert await store.is_platform_chat_ready("dc:user:222") is True


@pytest.mark.asyncio
async def test_link_code_single_use(store: ControlPlaneStore) -> None:
    user = await store.create_user(email="a@x.com", password="password1")
    _, token = await store.create_link_code(user.id)
    await store.consume_link_code(token, platform_key="tg:user:1")
    with pytest.raises(ValueError, match="already used|invalid"):
        await store.consume_link_code(token, platform_key="tg:user:2")


@pytest.mark.asyncio
async def test_unlink(store: ControlPlaneStore) -> None:
    user = await store.create_user(email="b@x.com", password="password1")
    _, t1 = await store.create_link_code(user.id, platform="telegram")
    await store.consume_link_code(t1, platform_key="tg:user:9")
    assert await store.unlink_platform(user.id, "telegram")
    assert await store.list_platform_links(user.id) == []
