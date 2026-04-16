"""Tests for IdentityResolver extracted from Hub.

Tests cover identity resolution, binding resolution, and trust re-resolution.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lyra.core.authenticator import Authenticator
from lyra.core.hub.hub_protocol import Binding, RoutingKey
from lyra.core.hub.identity_resolver import IdentityResolver
from lyra.core.message import InboundMessage, Platform
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_inbound(  # noqa: PLR0913
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    scope_id: str = "chat:42",
    trust_level: TrustLevel = TrustLevel.PUBLIC,
    is_admin: bool = False,
    roles: tuple[str, ...] = (),
) -> InboundMessage:
    """Build a minimal InboundMessage for IdentityResolver tests."""
    return InboundMessage(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={"chat_id": 42},
        trust_level=trust_level,
        is_admin=is_admin,
        roles=roles,
    )


def make_authenticator(
    default: TrustLevel = TrustLevel.PUBLIC,
    admin_user_ids: frozenset[str] = frozenset(),
    role_map: dict[str, TrustLevel] | None = None,
) -> Authenticator:
    """Build a minimal Authenticator for tests."""
    return Authenticator(
        store=None,
        role_map=role_map or {},
        default=default,
        admin_user_ids=admin_user_ids,
    )


# ---------------------------------------------------------------------------
# Test resolve_identity
# ---------------------------------------------------------------------------


class TestResolveIdentity:
    """Tests for IdentityResolver.resolve_identity."""

    def test_returns_public_when_no_authenticator(self) -> None:
        """When no authenticator is registered, returns PUBLIC identity."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        identity = resolver.resolve_identity("alice", "telegram", "main")
        assert identity.trust_level == TrustLevel.PUBLIC
        assert identity.is_admin is False
        assert identity.user_id == "alice"

    def test_returns_public_for_unknown_platform(self) -> None:
        """Unknown platform string returns PUBLIC identity."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        identity = resolver.resolve_identity("alice", "unknown_platform", "main")
        assert identity.trust_level == TrustLevel.PUBLIC

    def test_uses_authenticator_when_registered(self) -> None:
        """When authenticator is registered, uses it to resolve identity."""
        auth = make_authenticator(default=TrustLevel.TRUSTED)
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        identity = resolver.resolve_identity("alice", "telegram", "main")
        assert identity.trust_level == TrustLevel.TRUSTED

    def test_empty_user_id_returns_empty_string(self) -> None:
        """None user_id becomes empty string in identity."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        identity = resolver.resolve_identity(None, "telegram", "main")
        assert identity.user_id == ""

    def test_admin_user_id_from_authenticator(self) -> None:
        """Authenticator admin_user_ids are respected."""
        auth = make_authenticator(
            default=TrustLevel.PUBLIC,
            admin_user_ids=frozenset({"alice"}),
        )
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        identity = resolver.resolve_identity("alice", "telegram", "main")
        assert identity.is_admin is True


# ---------------------------------------------------------------------------
# Test resolve_binding
# ---------------------------------------------------------------------------


class TestResolveBinding:
    """Tests for IdentityResolver.resolve_binding."""

    def test_exact_binding_match(self) -> None:
        """Exact routing key match returns the binding."""
        bindings = {
            RoutingKey(Platform.TELEGRAM, "main", "chat:42"): Binding(
                agent_name="lyra", pool_id="telegram:main:chat:42"
            )
        }
        resolver = IdentityResolver(authenticators={}, bindings=bindings)
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:42")
        binding = resolver.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        assert binding.pool_id == "telegram:main:chat:42"

    def test_wildcard_fallback(self) -> None:
        """Wildcard binding is used when no exact match exists."""
        bindings = {
            RoutingKey(Platform.TELEGRAM, "main", "*"): Binding(
                agent_name="lyra", pool_id="telegram:main:*"
            )
        }
        resolver = IdentityResolver(authenticators={}, bindings=bindings)
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:99")
        binding = resolver.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        # Pool ID is synthesized from message scope, not wildcard
        assert binding.pool_id == "telegram:main:chat:99"

    def test_exact_takes_precedence_over_wildcard(self) -> None:
        """Exact binding is preferred over wildcard."""
        bindings = {
            RoutingKey(Platform.TELEGRAM, "main", "chat:42"): Binding(
                agent_name="exact_agent", pool_id="exact_pool"
            ),
            RoutingKey(Platform.TELEGRAM, "main", "*"): Binding(
                agent_name="wildcard_agent", pool_id="wildcard_pool"
            ),
        }
        resolver = IdentityResolver(authenticators={}, bindings=bindings)
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:42")
        binding = resolver.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "exact_agent"

    def test_no_binding_returns_none(self) -> None:
        """No matching binding returns None."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:42")
        assert resolver.resolve_binding(msg) is None

    def test_wildcard_does_not_bleed_across_platforms(self) -> None:
        """Wildcard on one platform does not match another platform."""
        bindings = {
            RoutingKey(Platform.DISCORD, "main", "*"): Binding(
                agent_name="discord_agent", pool_id="discord:main:*"
            )
        }
        resolver = IdentityResolver(authenticators={}, bindings=bindings)
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:42")
        assert resolver.resolve_binding(msg) is None

    def test_wildcard_does_not_bleed_across_bot_ids(self) -> None:
        """Wildcard for one bot_id does not match another bot_id."""
        bindings = {
            RoutingKey(Platform.TELEGRAM, "bot1", "*"): Binding(
                agent_name="bot1_agent", pool_id="telegram:bot1:*"
            )
        }
        resolver = IdentityResolver(authenticators={}, bindings=bindings)
        msg = make_inbound(platform="telegram", bot_id="bot2", scope_id="chat:42")
        assert resolver.resolve_binding(msg) is None


# ---------------------------------------------------------------------------
# Test resolve_message_trust
# ---------------------------------------------------------------------------


class TestResolveMessageTrust:
    """Tests for IdentityResolver.resolve_message_trust."""

    def test_no_change_when_no_authenticator(self) -> None:
        """Message is unchanged when no authenticator is registered."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        msg = make_inbound(trust_level=TrustLevel.PUBLIC, is_admin=False)
        result = resolver.resolve_message_trust(msg)
        assert result is msg  # Same object returned

    def test_updates_trust_from_authenticator(self) -> None:
        """Trust level is updated from authenticator resolution."""
        auth = make_authenticator(default=TrustLevel.TRUSTED)
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        msg = make_inbound(
            platform="telegram", bot_id="main", trust_level=TrustLevel.PUBLIC
        )
        result = resolver.resolve_message_trust(msg)
        assert result.trust_level == TrustLevel.TRUSTED

    def test_updates_admin_from_authenticator(self) -> None:
        """Admin status is updated from authenticator resolution."""
        auth = make_authenticator(
            default=TrustLevel.PUBLIC,
            admin_user_ids=frozenset({"alice"}),
        )
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        msg = make_inbound(
            platform="telegram",
            bot_id="main",
            user_id="alice",
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )
        result = resolver.resolve_message_trust(msg)
        assert result.is_admin is True

    def test_returns_same_object_when_unchanged(self) -> None:
        """When trust and admin are unchanged, same object is returned."""
        auth = make_authenticator(default=TrustLevel.PUBLIC)
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        msg = make_inbound(
            platform="telegram", bot_id="main", trust_level=TrustLevel.PUBLIC
        )
        result = resolver.resolve_message_trust(msg)
        assert result is msg

    def test_unknown_platform_returns_same_message(self) -> None:
        """Unknown platform string returns the message unchanged."""
        resolver = IdentityResolver(authenticators={}, bindings={})
        msg = make_inbound(platform="unknown", trust_level=TrustLevel.PUBLIC)
        result = resolver.resolve_message_trust(msg)
        assert result is msg

    def test_roles_passed_to_authenticator(self) -> None:
        """Roles from message are passed to authenticator.resolve()."""
        # Create authenticator with role-based trust mapping
        auth = make_authenticator(
            default=TrustLevel.PUBLIC,
            role_map={"moderator": TrustLevel.TRUSTED},
        )
        resolver = IdentityResolver(
            authenticators={(Platform.TELEGRAM, "main"): auth},
            bindings={},
        )
        # Message with moderator role should get TRUSTED level
        msg = make_inbound(
            platform="telegram",
            bot_id="main",
            trust_level=TrustLevel.PUBLIC,
            roles=("moderator",),
        )
        result = resolver.resolve_message_trust(msg)
        assert result.trust_level == TrustLevel.TRUSTED


# ---------------------------------------------------------------------------
# Integration: Hub delegation
# ---------------------------------------------------------------------------


class TestHubDelegation:
    """Tests that Hub correctly delegates to IdentityResolver."""

    def test_hub_resolve_identity_delegates(self) -> None:
        """Hub.resolve_identity delegates to IdentityResolver."""
        from lyra.core.hub import Hub

        hub = Hub()
        # Register an authenticator
        auth = make_authenticator(default=TrustLevel.TRUSTED)
        hub.register_authenticator(Platform.TELEGRAM, "main", auth)

        identity = hub.resolve_identity("alice", "telegram", "main")
        assert identity.trust_level == TrustLevel.TRUSTED

    def test_hub_resolve_binding_delegates(self) -> None:
        """Hub.resolve_binding delegates to IdentityResolver."""
        from lyra.core.hub import Hub

        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )
        msg = make_inbound(platform="telegram", bot_id="main", scope_id="chat:42")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"

    def test_hub_resolve_message_trust_delegates(self) -> None:
        """Hub._resolve_message_trust delegates to IdentityResolver."""
        from lyra.core.hub import Hub

        hub = Hub()
        auth = make_authenticator(default=TrustLevel.TRUSTED)
        hub.register_authenticator(Platform.TELEGRAM, "main", auth)

        msg = make_inbound(
            platform="telegram", bot_id="main", trust_level=TrustLevel.PUBLIC
        )
        result = hub._resolve_message_trust(msg)
        assert result.trust_level == TrustLevel.TRUSTED
