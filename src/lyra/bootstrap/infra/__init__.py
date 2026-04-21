"""Infra bootstrap — embedded NATS, health, lockfile, and startup notification."""

from .embedded_nats import ensure_nats
from .health import create_health_app
from .lockfile import acquire_lockfile, release_lockfile
from .notify import notify_startup

__all__ = [
    "ensure_nats",
    "create_health_app",
    "acquire_lockfile",
    "release_lockfile",
    "notify_startup",
]
