# pyright: reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""RED tests for ToolDisplayConfig threading — emitter + adapters (#1336 Slice 2 T12).

Tests assert that:
- TelegramAdapter / DiscordAdapter accept tool_display_config= kwarg (SC-2)
- OutboundAdapterBase.send_streaming injects the config into the emitter (SC-4)
- bash_max_len truncation is honored end-to-end (SC-1)
- show.<key>=False suppresses recap lines end-to-end (SC-2)
- Absent config falls back to ToolDisplayConfig() defaults (SC-4)

All 6 tests are intentionally RED until Wave 4 (T13-T17) lands:
- OutboundEmitter.tool_display_config attribute (T14)
- Lazy ToolRecapAccumulator(config=...) in run() (T14)
- OutboundAdapterBase.send_streaming injection (T13)
- TelegramAdapter.__init__ tool_display_config kwarg (T15)
- DiscordAdapter.__init__ tool_display_config kwarg (T16)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from lyra.core.messaging.message import InboundMessage, OutboundMessage
from lyra.core.messaging.render_events import (
    RenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.tool_display_config import ToolDisplayConfig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LONG_CMD = "x" * 250  # 250-char bash command, exceeds bash_max_len=200


async def _bash_event_stream(cmd: str) -> AsyncIterator[RenderEvent]:
    """Yield a minimal event sequence with one bash tool call."""
    yield ToolCallStartRenderEvent(tool_call_id="t1", tool_name="bash")
    yield ToolCallArgsRenderEvent(tool_call_id="t1", delta=json.dumps({"command": cmd}))
    yield ToolCallEndRenderEvent(tool_call_id="t1")


async def _web_fetch_event_stream() -> AsyncIterator[RenderEvent]:
    """Yield a minimal event sequence with one web_fetch tool call."""
    yield ToolCallStartRenderEvent(tool_call_id="t2", tool_name="web_fetch")
    yield ToolCallArgsRenderEvent(
        tool_call_id="t2", delta=json.dumps({"url": "https://example.com"})
    )
    yield ToolCallEndRenderEvent(tool_call_id="t2")


def _capture_recap_lines(edit_tool_recap_mock: AsyncMock) -> list[str]:
    """Extract the lines from the last done=True edit_tool_recap call."""
    for call in reversed(edit_tool_recap_mock.call_args_list):
        args = call.args
        kwargs = call.kwargs
        done = (
            kwargs.get("done")
            if "done" in kwargs
            else (args[2] if len(args) >= 3 else None)
        )
        raw_lines = (
            kwargs.get("lines")
            if "lines" in kwargs
            else (args[1] if len(args) >= 2 else [])
        )
        if done is True:
            return list(raw_lines or [])
    return []


# ---------------------------------------------------------------------------
# Telegram fixtures
# ---------------------------------------------------------------------------


def _make_tg_adapter(tool_display_config: ToolDisplayConfig | None = None):
    """Build a TelegramAdapter with the given tool_display_config.

    Constructs the adapter WITHOUT the kwarg (removed in #1468), then calls
    configure_tool_display() post-construction. Passing None skips the setter
    call so send_streaming falls back to ToolDisplayConfig() defaults.
    """
    from lyra.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token",
        inbound_bus=MagicMock(),
    )
    if tool_display_config is not None:
        adapter.configure_tool_display(tool_display_config)
    placeholder_msg = SimpleNamespace(message_id=100)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=placeholder_msg)
    bot.edit_message_text = AsyncMock()
    adapter.bot = bot
    return adapter, bot


def _make_tg_inbound(chat_id: int = 42, message_id: int = 10) -> InboundMessage:
    from datetime import datetime, timezone

    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import TelegramMeta

    return InboundMessage(
        id=f"telegram:tg:user:1:0:{message_id}",
        platform="telegram",
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(
            chat_id=chat_id,
            message_id=message_id,
            topic_id=None,
            is_group=False,
        ),
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# Discord fixtures
# ---------------------------------------------------------------------------


def _make_dc_adapter(tool_display_config: ToolDisplayConfig | None = None):
    """Build a DiscordAdapter with the given tool_display_config.

    Constructs the adapter WITHOUT the kwarg (removed in #1468), then calls
    configure_tool_display() post-construction. Passing None skips the setter
    call so send_streaming falls back to ToolDisplayConfig() defaults.
    """
    from lyra.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    if tool_display_config is not None:
        adapter.configure_tool_display(tool_display_config)
    placeholder = AsyncMock()
    placeholder.id = 200
    placeholder.edit = AsyncMock()

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(return_value=placeholder)

    adapter._resolve_channel = AsyncMock(return_value=mock_channel)
    return adapter, mock_channel, placeholder


def _make_dc_inbound(channel_id: int = 333) -> InboundMessage:
    from datetime import datetime, timezone

    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import DiscordMeta

    return InboundMessage(
        id="discord:dc:user:1:0:0",
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=channel_id,
            message_id=0,  # no-reply path (channel.send)
            thread_id=None,
            channel_type="text",
        ),
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# Helper: intercept edit_tool_recap at the emitter level
# ---------------------------------------------------------------------------


def _patch_edit_tool_recap(adapter) -> AsyncMock:
    """Monkey-patch the callbacks so we can capture edit_tool_recap calls.

    The emitter's _cb.edit_tool_recap is our observation point. We wrap it
    after _make_emitter is called via a patched send_streaming.
    """
    mock = AsyncMock()
    original_make_emitter = adapter._make_emitter

    def patched_make_emitter(original_msg, outbound):
        emitter = original_make_emitter(original_msg, outbound)
        emitter._cb.edit_tool_recap = mock
        # Also patch edit_tool_recap on trace placeholder side
        return emitter

    adapter._make_emitter = patched_make_emitter
    return mock


# ===========================================================================
# TEST 1 — Telegram + bash_max_len=200 truncation
# ===========================================================================


class TestTelegramBashMaxLen200:
    async def test_telegram_bash_max_len_200_truncation(self) -> None:
        """TelegramAdapter: bash_max_len=200 must truncate at 200, not default 80.

        Negative guard: if tool_display_config is NOT threaded (emitter uses
        defaults with bash_max_len=80), cmd is truncated to 80 chars.
        The '>80' assertion fails, exposing the missing wiring.
        """
        # Arrange — Wave 4 will add tool_display_config kwarg; RED until then
        config = ToolDisplayConfig(bash_max_len=200)
        adapter, _ = _make_tg_adapter(tool_display_config=config)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_tg_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg,
            _bash_event_stream(_LONG_CMD),
            outbound=outbound,
        )

        # Assert — done=True recap lines contain a bash line truncated within 200 chars
        # but retaining MORE than 80 chars (proving bash_max_len=200 is honored)
        import re

        lines = _capture_recap_lines(recap_mock)
        assert lines, "edit_tool_recap must have been called with done=True"
        bash_lines = [ln for ln in lines if "\U0001f4bb" in ln]
        assert bash_lines, f"Expected at least one bash line in recap: {lines}"
        for bash_line in bash_lines:
            match = re.search(r"`([^`]*)`", bash_line)
            if match:
                cmd_part = match.group(1)
                assert len(cmd_part) <= 200, (
                    f"bash cmd must be ≤200 chars (bash_max_len=200), "
                    f"got {len(cmd_part)}: {cmd_part!r}"
                )
                # Must retain >80 chars — proves 200-char config was used,
                # not the 80-char default. If threading is missing, this fails.
                assert len(cmd_part) > 80, (
                    f"bash cmd must retain >80 chars (bash_max_len=200 applied), "
                    f"got only {len(cmd_part)} chars: {cmd_part!r}."
                )


# ===========================================================================
# TEST 2 — Discord + bash_max_len=200 truncation
# ===========================================================================


class TestDiscordBashMaxLen200:
    async def test_discord_bash_max_len_200_truncation(self) -> None:
        """DiscordAdapter: bash_max_len=200 must truncate at 200, not default 80.

        Negative guard: same as Telegram. If tool_display_config is not
        threaded, default bash_max_len=80 applies; the '>80' assertion fails.
        """
        # Arrange — Wave 4 adds tool_display_config kwarg; RED until then
        config = ToolDisplayConfig(bash_max_len=200)
        adapter, _, _ = _make_dc_adapter(tool_display_config=config)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_dc_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg,
            _bash_event_stream(_LONG_CMD),
            outbound=outbound,
        )

        # Assert — bash line truncated within 200 chars but >80 (proving config honored)
        import re

        lines = _capture_recap_lines(recap_mock)
        assert lines, "edit_tool_recap must have been called with done=True"
        bash_lines = [ln for ln in lines if "\U0001f4bb" in ln]
        assert bash_lines, f"Expected at least one bash line in recap: {lines}"
        for bash_line in bash_lines:
            match = re.search(r"`([^`]*)`", bash_line)
            if match:
                cmd_part = match.group(1)
                assert len(cmd_part) <= 200, (
                    f"bash cmd must be ≤200 chars (bash_max_len=200), "
                    f"got {len(cmd_part)}: {cmd_part!r}"
                )
                assert len(cmd_part) > 80, (
                    f"bash cmd must retain >80 chars (bash_max_len=200 applied), "
                    f"got only {len(cmd_part)} chars: {cmd_part!r}."
                )


# ===========================================================================
# TEST 3 — Telegram + show.web_fetch=False suppresses recap lines
# ===========================================================================


class TestTelegramShowWebFetchFalse:
    async def test_telegram_show_web_fetch_false_suppresses(self) -> None:
        """TelegramAdapter with show.web_fetch=False must produce no web_fetch lines."""
        # Arrange — show overrides default True for web_fetch
        config = ToolDisplayConfig(show={"web_fetch": False})
        adapter, _ = _make_tg_adapter(tool_display_config=config)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_tg_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg,
            _web_fetch_event_stream(),
            outbound=outbound,
        )

        # Assert — no edit_tool_recap calls at all (web_fetch suppressed → accum empty)
        # OR calls happened but no line contains "example.com" or web-fetch indicator
        all_calls = recap_mock.call_args_list
        for call in all_calls:
            args = call.args
            kwargs = call.kwargs
            lines = (
                kwargs.get("lines")
                if "lines" in kwargs
                else (args[1] if len(args) >= 2 else [])
            )
            for line in lines or []:
                assert "example.com" not in line, (
                    "web_fetch URL must not appear in recap "
                    f"when show.web_fetch=False: {line!r}"
                )
                assert "\U0001f310" not in line, (
                    "web_fetch globe emoji must not appear in recap "
                    f"when show.web_fetch=False: {line!r}"
                )


# ===========================================================================
# TEST 4 — Discord + show.web_fetch=False suppresses recap lines
# ===========================================================================


class TestDiscordShowWebFetchFalse:
    async def test_discord_show_web_fetch_false_suppresses(self) -> None:
        """DiscordAdapter with show.web_fetch=False must produce no web_fetch lines."""
        config = ToolDisplayConfig(show={"web_fetch": False})
        adapter, _, _ = _make_dc_adapter(tool_display_config=config)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_dc_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg,
            _web_fetch_event_stream(),
            outbound=outbound,
        )

        # Assert — no web_fetch lines in any recap call
        all_calls = recap_mock.call_args_list
        for call in all_calls:
            args = call.args
            kwargs = call.kwargs
            lines = (
                kwargs.get("lines")
                if "lines" in kwargs
                else (args[1] if len(args) >= 2 else [])
            )
            for line in lines or []:
                assert "example.com" not in line, (
                    "web_fetch URL must not appear in recap "
                    f"when show.web_fetch=False: {line!r}"
                )
                assert "\U0001f310" not in line, (
                    "web_fetch globe emoji must not appear in recap "
                    f"when show.web_fetch=False: {line!r}"
                )


# ===========================================================================
# TEST 5 — Telegram + no config → defaults (bash_max_len=80)
# ===========================================================================


class TestTelegramNoConfigUsesDefaults:
    async def test_telegram_no_config_uses_defaults(self) -> None:
        """TelegramAdapter with tool_display_config=None must truncate at default 80."""
        # Arrange — no tool_display_config, adapter falls back to ToolDisplayConfig()
        adapter, _ = _make_tg_adapter(tool_display_config=None)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_tg_inbound()
        outbound = OutboundMessage.from_text("")

        # A command 100 chars long — exceeds default 80, must be truncated
        cmd_100 = "y" * 100

        # Act
        await adapter.send_streaming(
            original_msg,
            _bash_event_stream(cmd_100),
            outbound=outbound,
        )

        # Assert — bash cmd truncated at 80 (default), not passed through as-is
        lines = _capture_recap_lines(recap_mock)
        assert lines, "edit_tool_recap must fire with done=True"
        bash_lines = [ln for ln in lines if "\U0001f4bb" in ln]
        assert bash_lines, f"Expected at least one bash line in recap: {lines}"
        import re

        for bash_line in bash_lines:
            match = re.search(r"`([^`]*)`", bash_line)
            if match:
                cmd_part = match.group(1)
                assert len(cmd_part) <= 80, (
                    f"Default bash_max_len=80: cmd must be ≤80 chars, "
                    f"got {len(cmd_part)}: {cmd_part!r}"
                )
                assert cmd_part != cmd_100, (
                    "100-char cmd must be truncated by default config"
                )


# ===========================================================================
# TEST 6 — Discord + no config → defaults (bash_max_len=80)
# ===========================================================================


class TestDiscordNoConfigUsesDefaults:
    async def test_discord_no_config_uses_defaults(self) -> None:
        """DiscordAdapter with tool_display_config=None must truncate at default 80."""
        adapter, _, _ = _make_dc_adapter(tool_display_config=None)
        recap_mock = _patch_edit_tool_recap(adapter)
        original_msg = _make_dc_inbound()
        outbound = OutboundMessage.from_text("")

        cmd_100 = "z" * 100

        # Act
        await adapter.send_streaming(
            original_msg,
            _bash_event_stream(cmd_100),
            outbound=outbound,
        )

        # Assert — default bash_max_len=80 is applied
        lines = _capture_recap_lines(recap_mock)
        assert lines, "edit_tool_recap must fire with done=True"
        bash_lines = [ln for ln in lines if "\U0001f4bb" in ln]
        assert bash_lines, f"Expected at least one bash line in recap: {lines}"
        import re

        for bash_line in bash_lines:
            match = re.search(r"`([^`]*)`", bash_line)
            if match:
                cmd_part = match.group(1)
                assert len(cmd_part) <= 80, (
                    f"Default bash_max_len=80: cmd must be ≤80 chars, "
                    f"got {len(cmd_part)}: {cmd_part!r}"
                )
                assert cmd_part != cmd_100, (
                    "100-char cmd must be truncated by default config"
                )
