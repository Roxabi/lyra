"""Tests for core config dataclasses: HubConfig, PoolConfig, RouterConfig."""

import pytest

from factory.core.config import (
    BusConfig,
    HubConfig,
    PlatformConfig,
    PoolConfig,
    RouterConfig,
)
from factory.core.config.agent_defaults_config import AgentDefaultsConfig
from factory.core.config.dispatch_config import DispatchConfig
from factory.core.config.lifecycle_config import LifecycleConfig
from factory.core.lifecycle import session_lifecycle


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


class TestBusConfigWiring:
    """Verify HubConfig bus-sizing fields delegate to BusConfig — not bare literals."""

    def test_hub_config_staging_maxsize_delegates_to_bus_config(self) -> None:
        """HubConfig.staging_maxsize default == BusConfig.DEFAULT_STAGING_MAXSIZE."""
        assert HubConfig().staging_maxsize == BusConfig.DEFAULT_STAGING_MAXSIZE

    def test_hub_config_platform_queue_maxsize_delegates_to_bus_config(self) -> None:
        """HubConfig.platform_queue_maxsize default == BusConfig.DEFAULT_MAXSIZE."""
        assert HubConfig().platform_queue_maxsize == BusConfig.DEFAULT_MAXSIZE

    def test_hub_config_queue_depth_threshold_delegates_to_bus_config(self) -> None:
        """HubConfig.queue_depth_threshold default == BusConfig.DEFAULT_QUEUE_DEPTH."""
        assert HubConfig().queue_depth_threshold == BusConfig.DEFAULT_QUEUE_DEPTH


class TestPlatformConfigWiring:
    """Verify session_lifecycle module-level constants are wired to PlatformConfig."""

    def test_model_context_tokens_references_platform_config(self) -> None:
        """MODEL_CONTEXT_TOKENS is wired to PlatformConfig, not a bare literal."""
        assert (
            session_lifecycle.MODEL_CONTEXT_TOKENS
            == PlatformConfig.DEFAULT_CONTEXT_TOKENS
        )

    def test_session_manager_compact_context_tokens_references_platform_config(
        self,
    ) -> None:
        """SessionManager._compact_context_tokens is wired to PlatformConfig."""
        assert (
            session_lifecycle.SessionManager._compact_context_tokens
            == PlatformConfig.DEFAULT_CONTEXT_TOKENS
        )


class TestDispatchConfigWiring:
    """Verify _dispatch.py and middleware_stt.py delegate to DispatchConfig."""

    def test_backoff_delays_delegates_to_dispatch_config(self) -> None:
        """_dispatch._BACKOFF_DELAYS must equal DispatchConfig.BACKOFF_DELAYS."""
        from factory.core.hub.outbound._dispatch import _BACKOFF_DELAYS

        assert _BACKOFF_DELAYS == DispatchConfig.BACKOFF_DELAYS

    def test_max_attempts_delegates_to_dispatch_config(self) -> None:
        """_dispatch._MAX_ATTEMPTS must equal DispatchConfig.MAX_ATTEMPTS."""
        from factory.core.hub.outbound._dispatch import _MAX_ATTEMPTS

        assert _MAX_ATTEMPTS == DispatchConfig.MAX_ATTEMPTS

    def test_max_transcript_len_delegates_to_dispatch_config(self) -> None:
        """middleware_stt.MAX_TRANSCRIPT_LEN == DispatchConfig.MAX_TRANSCRIPT_LEN."""
        from factory.core.hub.middleware.middleware_stt import MAX_TRANSCRIPT_LEN

        assert MAX_TRANSCRIPT_LEN == DispatchConfig.MAX_TRANSCRIPT_LEN


class TestLifecycleConfigWiring:
    """Verify circuit_breaker.py and debouncer.py delegate to LifecycleConfig."""

    def test_circuit_breaker_default_failure_threshold_delegates_to_lifecycle_config(
        self,
    ) -> None:
        """CircuitBreaker() default failure_threshold == CIRCUIT_FAILURE_THRESHOLD."""
        from factory.core.lifecycle.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test-delegation")
        assert cb.failure_threshold == LifecycleConfig.CIRCUIT_FAILURE_THRESHOLD

    def test_circuit_breaker_default_recovery_timeout_delegates_to_lifecycle_config(
        self,
    ) -> None:
        """CircuitBreaker() default recovery_timeout == CIRCUIT_RECOVERY_TIMEOUT."""
        from factory.core.lifecycle.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test-delegation")
        assert cb.recovery_timeout == LifecycleConfig.CIRCUIT_RECOVERY_TIMEOUT

    def test_debouncer_default_debounce_ms_delegates_to_lifecycle_config(
        self,
    ) -> None:
        """debouncer.DEFAULT_DEBOUNCE_MS == LifecycleConfig.DEFAULT_DEBOUNCE_MS."""
        from factory.core.lifecycle.debouncer import DEFAULT_DEBOUNCE_MS

        assert DEFAULT_DEBOUNCE_MS == LifecycleConfig.DEFAULT_DEBOUNCE_MS


class TestAgentDefaultsConfigWiring:
    """Verify ModelConfig and agent_seeder defaults delegate to AgentDefaultsConfig."""

    def test_model_config_default_model_delegates_to_agent_defaults_config(
        self,
    ) -> None:
        """ModelConfig() default model == AgentDefaultsConfig.DEFAULT_MODEL."""
        from factory.core.ports.llm_types import ModelConfig

        assert ModelConfig().model == AgentDefaultsConfig.DEFAULT_MODEL

    def test_llm_types_default_model_literal_matches_agent_defaults_config(
        self,
    ) -> None:
        """_DEFAULT_MODEL in llm_types must equal AgentDefaultsConfig.DEFAULT_MODEL.

        This test fails if either side is updated without updating the other,
        ensuring the two independent literals stay in sync.
        """
        from factory.core.ports.llm_types import _DEFAULT_MODEL

        assert _DEFAULT_MODEL == AgentDefaultsConfig.DEFAULT_MODEL
