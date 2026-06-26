"""Platform-prefixed identity keys (tg:user:…, dc:user:…)."""

from __future__ import annotations

from typing import Final

__all__ = [
    "PLATFORM_PREFIXES",
    "format_platform_key",
    "is_platform_key",
    "is_user_id",
    "parse_platform_key",
]

USER_ID_PREFIX: Final[str] = "rx:user:"

PLATFORM_PREFIXES: Final[dict[str, str]] = {
    "tg:user:": "telegram",
    "dc:user:": "discord",
    "matrix:user:": "matrix",
}


def parse_platform_key(key: str) -> tuple[str, str, str] | None:
    """Parse a prefixed platform key into (platform, platform_uid, platform_key).

    Returns None when *key* is not a recognised platform identity.
    """
    for prefix, platform in PLATFORM_PREFIXES.items():
        if key.startswith(prefix):
            uid = key[len(prefix) :]
            if uid:
                return platform, uid, key
    return None


def format_platform_key(platform: str, platform_uid: str) -> str:
    """Build a canonical platform key from platform name and bare uid."""
    prefix = next(
        (p for p, name in PLATFORM_PREFIXES.items() if name == platform),
        None,
    )
    if prefix is None:
        raise ValueError(f"Unknown platform {platform!r}")
    return f"{prefix}{platform_uid}"


def is_platform_key(key: str) -> bool:
    return parse_platform_key(key) is not None


def is_user_id(key: str) -> bool:
    return key.startswith(USER_ID_PREFIX) and len(key) > len(USER_ID_PREFIX)