"""AgentGrantStore resolves grants via UserStore-linked principals."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.auth.agent_grants import Capability, Principal, PrincipalKind
from factory.core.auth.platform_keys import USER_ID_PREFIX
from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
from factory.infrastructure.stores.identity.user_store import UserStore


@pytest.mark.asyncio
async def test_grant_on_canonical_id_matches_platform_key(tmp_path: Path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    grant_store = AgentGrantStore(
        db_path=tmp_path / "auth.db", user_store=user_store
    )
    await user_store.connect()
    await grant_store.connect()
    try:
        user_id = await user_store.ensure_user("tg:user:1")
        await user_store.link_platform_keys("tg:user:1", "dc:user:2")
        await grant_store.grant(
            "lyra_default",
            Principal(PrincipalKind.USER, user_id),
            capability=Capability.USE,
            granted_by="test",
            source="test",
        )
        decision = grant_store.authorize(
            agent_name="lyra_default",
            user_id="dc:user:2",
        )
        assert decision.allowed is True
        assert user_id.startswith(USER_ID_PREFIX)
    finally:
        await grant_store.close()
        await user_store.close()