"""TrustLevel enum — authorization level for inbound messages."""

from __future__ import annotations

from enum import Enum


class TrustLevel(str, Enum):
    """Authorization level assigned to an inbound message sender."""

    OWNER = "owner"
    TRUSTED = "trusted"
    PUBLIC = "public"
    BLOCKED = "blocked"
