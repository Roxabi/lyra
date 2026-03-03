"""Thin re-export shim — configs live in their respective adapter modules."""
from lyra.adapters.discord import DiscordConfig, load_discord_config
from lyra.adapters.telegram import TelegramConfig, load_config

__all__ = ["DiscordConfig", "load_discord_config", "TelegramConfig", "load_config"]
