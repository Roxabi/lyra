"""Tests for /add-vault session command handler (issue #372).

Replaces the old AddVaultProcessor tests — the command is now a simple session
command that saves to vault and returns a static response (no LLM).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.commands.add_vault.handlers import (
    _MAX_CONTENT_CHARS,
    _NOTE_CATEGORY,
    _NOTE_TYPE,
    cmd_add_vault,
)
from lyra.core.commands.command_parser import CommandContext
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools, VaultWriteFailed

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
    cmd = CommandContext(prefix="/", name="add-vault", args=text, raw=f"/add-vault {text}")
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
        timestamp=None,
        trust_level=TrustLevel.TRUSTED,
        command=cmd,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAddVaultHappyPath:
    async def test_saves_note_to_vault(self) -> None:
        tools = make_tools()
        resp = await cmd_add_vault(make_msg("Buy milk"), _DUMMY_DRIVER, tools, ["Buy", "milk"], 30.0)
        cast(Any, tools.vault).add.assert_called_once()
        assert "saved" in resp.content.lower()

    async def test_saves_with_correct_category_and_type(self) -> None:
        tools = make_tools()
        calls: list = []
        cast(Any, tools.vault).add = AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw)))

        await cmd_add_vault(make_msg("Note"), _DUMMY_DRIVER, tools, ["Note"], 30.0)

        _, kwargs = calls[0]
        assert kwargs["category"] == _NOTE_CATEGORY
        assert kwargs["entry_type"] == _NOTE_TYPE

    async def test_title_truncated_to_80_chars(self) -> None:
        tools = make_tools()
        calls: list = []
        cast(Any, tools.vault).add = AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw)))
        long_note = "A" * 120

        await cmd_add_vault(make_msg(long_note), _DUMMY_DRIVER, tools, [long_note], 30.0)

        (title, *_), _ = calls[0]
        assert len(title) == 80

    async def test_full_content_passed_as_body(self) -> None:
        tools = make_tools()
        calls: list = []
        cast(Any, tools.vault).add = AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw)))

        await cmd_add_vault(make_msg("Short note"), _DUMMY_DRIVER, tools, ["Short", "note"], 30.0)

        (_, _, _, body), _ = calls[0]
        assert body == "Short note"

    async def test_empty_url_and_tags(self) -> None:
        tools = make_tools()
        calls: list = []
        cast(Any, tools.vault).add = AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw)))

        await cmd_add_vault(make_msg("Note"), _DUMMY_DRIVER, tools, ["Note"], 30.0)

        (_, tags, url, _), _ = calls[0]
        assert tags == []
        assert url == ""


# ---------------------------------------------------------------------------
# Missing content
# ---------------------------------------------------------------------------


class TestAddVaultMissingContent:
    async def test_empty_args_returns_usage(self) -> None:
        tools = make_tools()
        resp = await cmd_add_vault(make_msg(""), _DUMMY_DRIVER, tools, [], 30.0)
        assert "Usage:" in resp.content
        cast(Any, tools.vault).add.assert_not_called()

    async def test_whitespace_args_returns_usage(self) -> None:
        tools = make_tools()
        resp = await cmd_add_vault(make_msg(""), _DUMMY_DRIVER, tools, ["  ", "  "], 30.0)
        assert "Usage:" in resp.content
        cast(Any, tools.vault).add.assert_not_called()


# ---------------------------------------------------------------------------
# Content truncation
# ---------------------------------------------------------------------------


class TestAddVaultTruncation:
    async def test_oversized_content_truncated(self) -> None:
        tools = make_tools()
        calls: list = []
        cast(Any, tools.vault).add = AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw)))
        oversized = "B" * (_MAX_CONTENT_CHARS + 500)

        await cmd_add_vault(make_msg(oversized), _DUMMY_DRIVER, tools, [oversized], 30.0)

        (_, _, _, body), _ = calls[0]
        assert len(body) <= _MAX_CONTENT_CHARS + len("\n\n[note truncated]")
        assert "[note truncated]" in body


# ---------------------------------------------------------------------------
# Vault failures
# ---------------------------------------------------------------------------


class TestAddVaultFailures:
    async def test_vault_not_available(self) -> None:
        tools = make_tools(vault_side_effect=VaultWriteFailed("not_available"))
        resp = await cmd_add_vault(make_msg("Note"), _DUMMY_DRIVER, tools, ["Note"], 30.0)
        assert "not available" in resp.content.lower()
        assert "NOT saved" in resp.content

    async def test_vault_subprocess_error(self) -> None:
        tools = make_tools(vault_side_effect=VaultWriteFailed("subprocess_error"))
        resp = await cmd_add_vault(make_msg("Note"), _DUMMY_DRIVER, tools, ["Note"], 30.0)
        assert "NOT saved" in resp.content
