"""Tests for /add-vault session command handler (issue #372).

Replaces the old AddVaultProcessor tests — the command is now a simple session
command that saves to vault and returns a static response (no LLM).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from lyra.commands.add_vault.handlers import (
    _MAX_CONTENT_CHARS,
    _NOTE_CATEGORY,
    _NOTE_TYPE,
    cmd_add_vault,
)
from lyra.core.auth.trust import TrustLevel
from lyra.core.commands.command_parser import CommandContext
from lyra.core.exceptions import VaultWriteFailed
from lyra.core.messaging.message import InboundMessage
from lyra.integrations.base import SessionTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_DRIVER = MagicMock()


def make_tools(vault_side_effect=None) -> SessionTools:
    scraper = MagicMock()
    vault = MagicMock()
    if vault_side_effect is not None:
        vault.add = AsyncMock(side_effect=vault_side_effect)
    else:
        vault.add = AsyncMock()
    vault.search = AsyncMock(return_value="results")
    return SessionTools(scraper=scraper, vault=vault)


def make_msg(text: str = "hello") -> InboundMessage:
    cmd = CommandContext(
        prefix="/",
        name="add-vault",
        args=text,
        raw=f"/add-vault {text}",
    )
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text=f"/add-vault {text}",
        text_raw=f"/add-vault {text}",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        command=cmd,
    )


def _call(tools: SessionTools) -> list:
    calls: list = []
    cast(Any, tools.vault).add = AsyncMock(
        side_effect=lambda *a, **kw: calls.append((a, kw)),
    )
    return calls


async def _run(msg, tools, args):
    return await cmd_add_vault(
        msg,
        _DUMMY_DRIVER,
        tools,
        args,
        30.0,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAddVaultHappyPath:
    async def test_saves_note_to_vault(self) -> None:
        tools = make_tools()
        resp = await _run(
            make_msg("Buy milk"),
            tools,
            ["Buy", "milk"],
        )
        cast(Any, tools.vault).add.assert_called_once()
        assert "saved" in resp.content.lower()

    async def test_saves_with_correct_category_and_type(self) -> None:
        tools = make_tools()
        calls = _call(tools)

        await _run(make_msg("Note"), tools, ["Note"])

        _, kwargs = calls[0]
        assert kwargs["category"] == _NOTE_CATEGORY
        assert kwargs["entry_type"] == _NOTE_TYPE

    async def test_title_truncated_to_80_chars(self) -> None:
        tools = make_tools()
        calls = _call(tools)
        long_note = "A" * 120

        await _run(make_msg(long_note), tools, [long_note])

        (title, *_), _ = calls[0]
        assert len(title) == 80

    async def test_full_content_passed_as_body(self) -> None:
        tools = make_tools()
        calls = _call(tools)

        await _run(
            make_msg("Short note"),
            tools,
            ["Short", "note"],
        )

        (_, _, _, body), _ = calls[0]
        assert body == "Short note"

    async def test_empty_url_and_tags(self) -> None:
        tools = make_tools()
        calls = _call(tools)

        await _run(make_msg("Note"), tools, ["Note"])

        (_, tags, url, _), _ = calls[0]
        assert tags == []
        assert url == ""


# ---------------------------------------------------------------------------
# Missing content
# ---------------------------------------------------------------------------


class TestAddVaultMissingContent:
    async def test_empty_args_returns_usage(self) -> None:
        tools = make_tools()
        resp = await _run(make_msg(""), tools, [])
        assert "Usage:" in resp.content
        cast(Any, tools.vault).add.assert_not_called()

    async def test_whitespace_args_returns_usage(self) -> None:
        tools = make_tools()
        resp = await _run(
            make_msg(""),
            tools,
            ["  ", "  "],
        )
        assert "Usage:" in resp.content
        cast(Any, tools.vault).add.assert_not_called()


# ---------------------------------------------------------------------------
# Content truncation
# ---------------------------------------------------------------------------


class TestAddVaultTruncation:
    async def test_oversized_content_truncated(self) -> None:
        tools = make_tools()
        calls = _call(tools)
        oversized = "B" * (_MAX_CONTENT_CHARS + 500)

        await _run(
            make_msg(oversized),
            tools,
            [oversized],
        )

        (_, _, _, body), _ = calls[0]
        max_len = _MAX_CONTENT_CHARS + len("\n\n[note truncated]")
        assert len(body) <= max_len
        assert "[note truncated]" in body


# ---------------------------------------------------------------------------
# Vault failures
# ---------------------------------------------------------------------------


class TestAddVaultFailures:
    async def test_vault_not_available(self) -> None:
        tools = make_tools(
            vault_side_effect=VaultWriteFailed("not_available"),
        )
        resp = await _run(
            make_msg("Note"),
            tools,
            ["Note"],
        )
        assert "not available" in resp.content.lower()
        assert "NOT saved" in resp.content

    async def test_vault_subprocess_error(self) -> None:
        tools = make_tools(
            vault_side_effect=VaultWriteFailed("subprocess_error"),
        )
        resp = await _run(
            make_msg("Note"),
            tools,
            ["Note"],
        )
        assert "NOT saved" in resp.content
