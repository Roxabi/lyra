"""Thin re-export shim — Telegram config moved to lyra.adapters.telegram."""
from lyra.adapters.telegram import TelegramConfig, load_config

__all__ = ["TelegramConfig", "load_config"]
