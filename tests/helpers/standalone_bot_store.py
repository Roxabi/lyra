"""Patch helpers for standalone adapter KV roster loading in tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from factory.config import (
    DISCORD_DEFAULT_AUTO_THREAD,
    DISCORD_DEFAULT_THREAD_HOT_HOURS,
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)

_PATCH_TG = "factory.bootstrap.wiring.standalone_telegram.seed_bot_roster"
_PATCH_DC = "factory.bootstrap.wiring.standalone_discord.seed_bot_roster"


def telegram_roster(bot_ids: list[str], **bot_kwargs: object) -> TelegramMultiConfig:
    return TelegramMultiConfig(
        bots=[
            TelegramBotConfig(bot_id=bot_id, **bot_kwargs)  # type: ignore[arg-type]
            for bot_id in bot_ids
        ]
    )


def discord_roster(
    bot_ids: list[str],
    *,
    auto_thread: bool = DISCORD_DEFAULT_AUTO_THREAD,
    thread_hot_hours: int = DISCORD_DEFAULT_THREAD_HOT_HOURS,
) -> DiscordMultiConfig:
    return DiscordMultiConfig(
        bots=[
            DiscordBotConfig(
                bot_id=bot_id,
                auto_thread=auto_thread,
                thread_hot_hours=thread_hot_hours,
            )
            for bot_id in bot_ids
        ]
    )


def patch_telegram_roster(monkeypatch: pytest.MonkeyPatch, bot_ids: list[str]) -> None:
    monkeypatch.setattr(
        _PATCH_TG,
        AsyncMock(return_value=telegram_roster(bot_ids)),
    )


def patch_discord_roster(
    monkeypatch: pytest.MonkeyPatch,
    bot_ids: list[str],
    *,
    auto_thread: bool = False,
    thread_hot_hours: int = DISCORD_DEFAULT_THREAD_HOT_HOURS,
) -> None:
    monkeypatch.setattr(
        _PATCH_DC,
        AsyncMock(
            return_value=discord_roster(
                bot_ids,
                auto_thread=auto_thread,
                thread_hot_hours=thread_hot_hours,
            )
        ),
    )
