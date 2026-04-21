"""Wiring bootstrap — adapter and NATS wiring helpers."""

from .bootstrap_wiring import wire_discord_adapters, wire_telegram_adapters
from .nats_wiring import wire_nats_discord_proxies, wire_nats_telegram_proxies

__all__ = [
    "wire_discord_adapters",
    "wire_telegram_adapters",
    "wire_nats_discord_proxies",
    "wire_nats_telegram_proxies",
]
