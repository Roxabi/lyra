"""Tests for /routing admin command (#134)."""

from __future__ import annotations

from unittest.mock import MagicMock

from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_router import CommandRouter
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel
from lyra.llm.smart_routing import SmartRoutingDecorator

from .conftest import _make_config, _make_inner, make_model_cfg

_cmd_parser = CommandParser()


def _make_admin_msg(
    text: str = "/routing",
    user_id: str = "tg:user:123",
    *,
    is_admin: bool = True,
) -> InboundMessage:
    return InboundMessage(
        id="msg1",
        platform="telegram",
        bot_id="bot1",
        scope_id="scope1",
        user_id=user_id,
        user_name="admin",
        is_mention=False,
        text=text,
        text_raw=text,
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=_cmd_parser.parse(text),
    )


class TestRoutingCommand:
    def _make_router(
        self,
        *,
        decorator: SmartRoutingDecorator | None = None,
    ) -> CommandRouter:
        loader = MagicMock(spec=CommandLoader)
        loader.get_commands.return_value = {}
        return CommandRouter(
            command_loader=loader,
            enabled_plugins=[],
            smart_routing_decorator=decorator,
        )

    async def test_routing_admin_only(self) -> None:
        """Non-admin users get rejected."""
        # Arrange
        router = self._make_router()
        msg = _make_admin_msg("/routing", is_admin=False)

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert resp is not None
        assert "admin-only" in resp.content

    async def test_routing_not_configured(self) -> None:
        """When no smart routing decorator, inform user."""
        # Arrange
        router = self._make_router(decorator=None)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert resp is not None
        assert "not configured" in resp.content

    async def test_routing_empty_history(self) -> None:
        """When history is empty, show appropriate message."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        dec = SmartRoutingDecorator(inner, config)
        router = self._make_router(decorator=dec)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert resp is not None
        assert "No routing decisions" in resp.content

    async def test_routing_shows_history(self) -> None:
        """After routing, /routing shows decisions."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        dec = SmartRoutingDecorator(inner, config)
        await dec.complete("p1", "hello", make_model_cfg(), "sys")
        router = self._make_router(decorator=dec)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert resp is not None
        assert "trivial" in resp.content
        assert "haiku" in resp.content
        assert "1 decisions" in resp.content
