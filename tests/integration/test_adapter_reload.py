"""Integration tests: adapter reload preserves Hub state.

Verifies that re-registering an adapter (simulating a reload) does not
disturb agent_registry, bindings, pools, or other adapter registrations
on the Hub.
"""

from __future__ import annotations

from lyra.core.hub.hub_protocol import RoutingKey
from lyra.core.message import Platform
from tests.core.conftest import _make_hub, _MockAdapter


class TestAdapterReloadPreservesHubState:
    def test_agent_registry_intact_after_adapter_reload(self) -> None:
        hub = _make_hub()
        # _make_hub pre-registers "lyra" agent + telegram adapter + binding
        assert "lyra" in hub.agent_registry

        new_adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", new_adapter)

        assert "lyra" in hub.agent_registry

    def test_bindings_intact_after_adapter_reload(self) -> None:
        hub = _make_hub()
        key = RoutingKey(Platform.TELEGRAM, "main", "*")
        assert key in hub.bindings

        new_adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", new_adapter)

        assert key in hub.bindings

    def test_second_adapter_unaffected_by_telegram_reload(self) -> None:
        hub = _make_hub()
        discord_adapter = _MockAdapter()
        hub.register_adapter(Platform.DISCORD, "main", discord_adapter)

        new_telegram_adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", new_telegram_adapter)

        assert hub.adapter_registry[(Platform.DISCORD, "main")] is discord_adapter

    def test_pool_survives_adapter_reload(self) -> None:
        hub = _make_hub()
        pool_id = "telegram:main:chat:42"
        hub.get_or_create_pool(pool_id, "lyra")
        assert pool_id in hub.pools

        new_adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", new_adapter)

        assert pool_id in hub.pools

    def test_new_adapter_instance_takes_effect(self) -> None:
        hub = _make_hub()
        old_adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]

        new_adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", new_adapter)

        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is new_adapter
        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is not old_adapter
