"""NATS connection helper with optional nkey authentication."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import nats
from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def _read_nkey_seed() -> str | None:
    """Read nkey seed from file path in NATS_NKEY_SEED_PATH env var.

    Returns None if env var is unset (dev mode).
    Exits with clear error if path is not a file, unreadable, or empty.
    """
    path_str = os.environ.get("NATS_NKEY_SEED_PATH")
    if not path_str:
        return None
    path = Path(path_str)
    try:
        if not path.is_file():
            sys.exit(
                f"NATS_NKEY_SEED_PATH={path_str!r} is not a file"
            )
        seed = path.read_text().strip()
    except OSError as exc:
        sys.exit(
            f"NATS_NKEY_SEED_PATH={path_str!r} is unreadable:"
            f" {exc.strerror}"
        )
    if not seed:
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is empty")
    return seed


_RESERVED_AUTH_KEYS = frozenset({"nkeys_seed_str", "token", "user", "password", "tls"})


def scrub_nats_url(url: str) -> str:
    """Return *url* with any embedded credentials removed.

    ``nats://user:pass@host:4222`` → ``nats://host:4222``
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    clean_netloc = parsed.hostname
    if parsed.port:
        clean_netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=clean_netloc))


async def _default_error_cb(exc: Exception) -> None:
    log.error("NATS error: %s", exc)


async def _default_disconnected_cb() -> None:
    log.warning("NATS disconnected")


async def _default_reconnected_cb() -> None:
    log.info("NATS reconnected")


async def nats_connect(url: str, **extra: Any) -> NATS:
    """Connect to NATS, optionally authenticating with an nkey seed.

    If NATS_NKEY_SEED_PATH is set, reads the seed file and passes it
    via nkeys_seed_str. If unset, connects without authentication (dev mode).

    Extra keyword arguments (e.g. ``error_cb``, ``disconnected_cb``,
    ``reconnected_cb``) are forwarded to ``nats.connect()``.
    Auth-related keys (``nkeys_seed_str``, ``token``, ``user``, ``password``,
    ``tls``) are rejected — authentication is owned exclusively by this helper.
    """
    bad = _RESERVED_AUTH_KEYS & extra.keys()
    if bad:
        raise ValueError(
            f"nats_connect: auth keys must not be passed via **extra: {bad}"
        )
    kwargs: dict[str, Any] = {
        "error_cb": _default_error_cb,
        "disconnected_cb": _default_disconnected_cb,
        "reconnected_cb": _default_reconnected_cb,
        **extra,
    }
    seed = _read_nkey_seed()
    if seed:
        kwargs["nkeys_seed_str"] = seed
    nc = await nats.connect(url, **kwargs)
    if seed:
        log.info("NATS nkey auth enabled (seed from NATS_NKEY_SEED_PATH)")
    return nc
