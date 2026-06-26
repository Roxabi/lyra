"""Wiring bootstrap — adapter and NATS wiring helpers."""

from .bootstrap_wiring import wire_discord_adapters, wire_telegram_adapters
from .nats_wiring import NatsProxyWiringDeps, wire_nats_proxies

__all__ = [
    "wire_discord_adapters",
    "wire_telegram_adapters",
    "NatsProxyWiringDeps",
    "wire_nats_proxies",
]