"""PlatformLinkMiddleware — dual-link chat gate (ADR-103 Block 9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.auth.trust import TrustLevel
from factory.core.hub.middleware.middleware_guards import PlatformLinkMiddleware
from factory.core.hub.pipeline.pipeline_types import Action
from factory.core.messaging.message import InboundMessage, Platform


def _msg(**kwargs) -> InboundMessage:
    base = dict(
        id="m1",
        platform=Platform.TELEGRAM.value,
        bot_id="b1",
        scope_id="s1",
        user_id="tg:user:1",
        user_name="tester",
        is_mention=False,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
    )
    base.update(kwargs)
    return InboundMessage(**base)


@pytest.mark.asyncio
async def test_skips_when_no_checker() -> None:
    mw = PlatformLinkMiddleware()
    hub = MagicMock()
    hub._platform_link_checker = None
    ctx = MagicMock()
    ctx.hub = hub
    nxt = AsyncMock(return_value="ok")
    out = await mw(_msg(), ctx, nxt)
    assert out == "ok"
    nxt.assert_awaited_once()


@pytest.mark.asyncio
async def test_allows_link_command_when_unlinked() -> None:
    mw = PlatformLinkMiddleware()
    checker = AsyncMock()
    checker.is_platform_chat_ready = AsyncMock(return_value=False)
    hub = MagicMock()
    hub._platform_link_checker = checker
    ctx = MagicMock()
    ctx.hub = hub
    nxt = AsyncMock(return_value="ok")
    out = await mw(_msg(text="/link abc", text_raw="/link abc"), ctx, nxt)
    assert out == "ok"
    checker.is_platform_chat_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_unlinked_chat() -> None:
    mw = PlatformLinkMiddleware()
    checker = AsyncMock()
    checker.is_platform_chat_ready = AsyncMock(return_value=False)
    hub = MagicMock()
    hub._platform_link_checker = checker
    ctx = MagicMock()
    ctx.hub = hub
    ctx.emit = MagicMock()
    nxt = AsyncMock()
    result = await mw(_msg(text="hi there", text_raw="hi there"), ctx, nxt)
    nxt.assert_not_awaited()
    assert result.action is Action.COMMAND_HANDLED
    assert result.response is not None
    assert "not linked" in result.response.content.lower()


@pytest.mark.asyncio
async def test_allows_chat_ready() -> None:
    mw = PlatformLinkMiddleware()
    checker = AsyncMock()
    checker.is_platform_chat_ready = AsyncMock(return_value=True)
    hub = MagicMock()
    hub._platform_link_checker = checker
    ctx = MagicMock()
    ctx.hub = hub
    nxt = AsyncMock(return_value="ok")
    out = await mw(_msg(text="hello", text_raw="hello"), ctx, nxt)
    assert out == "ok"


@pytest.mark.asyncio
async def test_web_platform_exempt() -> None:
    mw = PlatformLinkMiddleware()
    checker = AsyncMock()
    hub = MagicMock()
    hub._platform_link_checker = checker
    ctx = MagicMock()
    ctx.hub = hub
    nxt = AsyncMock(return_value="ok")
    out = await mw(
        _msg(
            platform=Platform.WEB.value,
            user_id="web:user:1",
            text="hi",
            text_raw="hi",
        ),
        ctx,
        nxt,
    )
    assert out == "ok"
    checker.is_platform_chat_ready.assert_not_awaited()
