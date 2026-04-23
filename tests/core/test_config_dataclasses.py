"""Tests for core config dataclasses: HubConfig, PoolConfig, RouterConfig."""

import pytest

from lyra.core.config import HubConfig, PoolConfig, RouterConfig


class TestHubConfig:
    """Tests for HubConfig frozen dataclass."""

    def test_default_values(self) -> None:
        """Default HubConfig has expected defaults."""
        config = HubConfig()
        assert config.rate_limit == 20
        assert config.rate_window == 60
        assert config.pool_ttl == 604800.0
        assert config.debounce_ms == 0
        assert config.cancel_on_new_message is False
        assert config.turn_timeout is None
        assert config.safe_dispatch_timeout == 10.0
        assert config.staging_maxsize == 500
        assert config.platform_queue_maxsize == 100
        assert config.queue_depth_threshold == 100
        assert config.max_merged_chars == 4096

    def test_custom_values(self) -> None:
        """HubConfig accepts custom values."""
        config = HubConfig(rate_limit=100, pool_ttl=3600.0, debounce_ms=500)
        assert config.rate_limit == 100
        assert config.pool_ttl == 3600.0
        assert config.debounce_ms == 500
        # Defaults preserved
        assert config.rate_window == 60

    def test_frozen_immutability(self) -> None:
        """HubConfig is frozen and cannot be modified."""
        config = HubConfig()
        with pytest.raises(AttributeError):
            config.rate_limit = 50  # type: ignore[misc]

    def test_equality(self) -> None:
        """HubConfig instances with same values are equal."""
        config1 = HubConfig(rate_limit=50)
        config2 = HubConfig(rate_limit=50)
        assert config1 == config2


class TestPoolConfig:
    """Tests for PoolConfig frozen dataclass."""

    def test_default_values(self) -> None:
        """Default PoolConfig has expected defaults."""
        config = PoolConfig()
        assert config.turn_timeout is None
        assert config.debounce_ms == 300
        assert config.turn_timeout_ceiling is None
        assert config.safe_dispatch_timeout == 10.0
        assert config.max_merged_chars == 4096
        assert config.cancel_on_new_message is False

    def test_custom_values(self) -> None:
        """PoolConfig accepts custom values."""
        config = PoolConfig(debounce_ms=500, turn_timeout=60.0)
        assert config.debounce_ms == 500
        assert config.turn_timeout == 60.0

    def test_frozen_immutability(self) -> None:
        """PoolConfig is frozen and cannot be modified."""
        config = PoolConfig()
        with pytest.raises(AttributeError):
            config.debounce_ms = 100  # type: ignore[misc]


class TestRouterConfig:
    """Tests for RouterConfig frozen dataclass."""

    def test_default_values(self) -> None:
        """Default RouterConfig has expected defaults."""
        config = RouterConfig()
        assert config.builtins == {}
        assert config.workspaces == {}
        assert config.patterns == {}
        # pattern_configs is lazy-loaded, just verify it's a dict
        assert isinstance(config.pattern_configs, dict)
        assert config.on_debounce_change is None
        assert config.on_cancel_change is None
        assert config.session_driver is None

    def test_custom_values(self) -> None:
        """RouterConfig accepts custom values."""
        config = RouterConfig(
            patterns={"bare_url": True},
            on_debounce_change=lambda x: None,
        )
        assert config.patterns == {"bare_url": True}
        assert config.on_debounce_change is not None

    def test_frozen_immutability(self) -> None:
        """RouterConfig is frozen and cannot be modified."""
        config = RouterConfig()
        with pytest.raises(AttributeError):
            config.patterns = {"test": True}  # type: ignore[misc]

    def test_pattern_configs_lazy_loads(self) -> None:
        """RouterConfig.pattern_configs lazy loads without import errors."""
        # This tests that the lazy loading works correctly
        config = RouterConfig()
        # Should not raise any errors
        assert isinstance(config.pattern_configs, dict)
