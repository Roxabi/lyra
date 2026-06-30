"""Canonical user identity models — one person, many platform identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["PlatformIdentity", "User"]


@dataclass(frozen=True)
class User:
    """Internal person record (canonical id: ``rx:user:<hex>``)."""

    id: str
    display_name: str | None
    email: str | None
    created_at: datetime


@dataclass(frozen=True)
class PlatformIdentity:
    """Binding between a platform account and an internal user."""

    platform_key: str
    platform: str
    platform_uid: str
    user_id: str
    linked_at: datetime