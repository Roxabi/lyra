"""grant_human_platforms helper (ADR-103 Block 10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.auth.agent_grants import Capability, PrincipalKind
from factory.core.auth.grant_human import grant_human_platforms


@pytest.mark.asyncio
async def test_grants_all_platform_keys() -> None:
    store = AsyncMock()
    store.grant = AsyncMock()
    resolver = MagicMock()
    resolver.resolve_platform_keys.return_value = frozenset(
        {"tg:user:1", "dc:user:2"}
    )
    granted = await grant_human_platforms(
        grant_store=store,
        key_resolver=resolver,
        agent_name="lyra",
        dash_user_id="rx:user:abc",
        granted_by="rx:user:admin",
    )
    assert set(granted) == {"tg:user:1", "dc:user:2"}
    assert store.grant.await_count == 2
    for call in store.grant.await_args_list:
        assert call.args[0] == "lyra"
        assert call.args[1].kind is PrincipalKind.USER
        assert call.kwargs["capability"] is Capability.USE
        assert call.kwargs["granted_by"] == "rx:user:admin"


@pytest.mark.asyncio
async def test_requires_links() -> None:
    store = AsyncMock()
    resolver = MagicMock()
    resolver.resolve_platform_keys.return_value = frozenset()
    with pytest.raises(ValueError, match="no linked"):
        await grant_human_platforms(
            grant_store=store,
            key_resolver=resolver,
            agent_name="lyra",
            dash_user_id="rx:user:abc",
            granted_by="admin",
        )
