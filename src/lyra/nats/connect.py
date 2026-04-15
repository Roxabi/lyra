"""NATS connection helper with optional nkey authentication and TLS."""

from __future__ import annotations

import logging
import os
import ssl
import sys
from typing import Any
from urllib.parse import urlparse, urlunparse

import nats
from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def _read_nkey_seed() -> str | None:
    """Read nkey seed from file path in NATS_NKEY_SEED_PATH env var.

    Returns None if env var is unset (dev mode).
    Exits with clear error if path is not a regular file, unreadable, empty,
    or has unsafe permissions (any group/world access bit).

    Security (TOCTOU): the path is opened once with ``O_NOFOLLOW`` (symlinks
    rejected) and ``os.fstat`` inspects the live file descriptor so the
    permission check and the read operate on the same inode. A parent-
    directory writer cannot swap the path between stat and read.
    """
    import errno
    import stat as _stat

    path_str = os.environ.get("NATS_NKEY_SEED_PATH")
    if not path_str:
        return None
    fd = -1
    try:
        fd = os.open(path_str, os.O_RDONLY | os.O_NOFOLLOW)
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is not a file")
        mode = st.st_mode & 0o777
        # Reject any group/world access (0o077) — owner-only reads are safe
        # whether they're 0o600 (rw) or 0o400 (r-only).
        if mode & 0o077:
            sys.exit(
                f"NATS_NKEY_SEED_PATH={path_str!r} has unsafe permissions"
                f" {oct(mode)} (group/world access forbidden; use 0o600 or 0o400)"
            )
        with os.fdopen(fd, "r") as fh:
            fd = -1  # fdopen takes ownership; prevent double-close below
            seed = fh.read().strip()
    except OSError as exc:
        if fd != -1:
            os.close(fd)
        if exc.errno in (errno.ENOENT, errno.ELOOP):
            # Missing path, or a symlink rejected by O_NOFOLLOW.
            sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is not a file")
        strerror = exc.strerror or str(exc)
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is unreadable: {strerror}")
    if not seed:
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is empty")
    return seed


_RESERVED_KEYS = frozenset({"nkeys_seed_str", "token", "user", "password", "tls"})


def _build_tls_context() -> ssl.SSLContext | None:
    """Build a TLS context from NATS_CA_CERT env var.

    Returns None if env var is unset (plain TCP / dev mode).
    Exits with clear error if path is not a regular file, unreadable, or empty.

    Security (TOCTOU): the path is opened once with ``O_NOFOLLOW`` (symlinks
    rejected) and ``os.fstat`` inspects the live file descriptor so the
    is-file check and the read operate on the same inode. Cert data is
    loaded via ``cadata=`` so ssl never re-opens the path. CA certs are not
    secret, so no file-mode restriction is imposed.
    """
    import errno
    import stat as _stat

    ca_path_str = os.environ.get("NATS_CA_CERT")
    if not ca_path_str:
        return None
    fd = -1
    try:
        fd = os.open(ca_path_str, os.O_RDONLY | os.O_NOFOLLOW)
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            sys.exit(f"NATS_CA_CERT={ca_path_str!r} is not a file")
        with os.fdopen(fd, "r") as fh:
            fd = -1  # fdopen takes ownership; prevent double-close below
            ca_data = fh.read().strip()
    except OSError as exc:
        if fd != -1:
            os.close(fd)
        if exc.errno in (errno.ENOENT, errno.ELOOP):
            # Missing path, or a symlink rejected by O_NOFOLLOW.
            sys.exit(f"NATS_CA_CERT={ca_path_str!r} is not a file")
        strerror = exc.strerror or str(exc)
        sys.exit(f"NATS_CA_CERT={ca_path_str!r} is unreadable: {strerror}")
    if not ca_data:
        sys.exit(f"NATS_CA_CERT={ca_path_str!r} is empty")
    try:
        ctx = ssl.create_default_context(cadata=ca_data)
    except ssl.SSLError as exc:
        # Use exc.reason only — avoid leaking OpenSSL internals (file paths,
        # library build details) that would surface via the default str(exc).
        reason = exc.reason or "invalid PEM"
        sys.exit(f"NATS_CA_CERT={ca_path_str!r} is not a valid PEM bundle: {reason}")
    return ctx


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
    bad = _RESERVED_KEYS & extra.keys()
    if bad:
        raise ValueError(
            f"nats_connect: reserved keys must not be passed via **extra: {bad}"
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
    tls_ctx = _build_tls_context()
    if tls_ctx:
        kwargs["tls"] = tls_ctx
    nc = await nats.connect(url, **kwargs)
    if seed:
        log.info("NATS nkey auth enabled (seed from NATS_NKEY_SEED_PATH)")
    if tls_ctx:
        log.info("NATS TLS enabled (CA from NATS_CA_CERT)")
    return nc
