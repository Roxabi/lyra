"""Backward-compat re-export — testing guards migrated to roxabi_nats.testing._guards.

ADR-059 V6: canonical location is now ``roxabi_nats.testing._guards``.
"""

from roxabi_nats.testing._guards import (  # noqa: F401
    ALLOWED_LOOPBACK_HOSTS,
    _assert_loopback_url,
    _assert_not_production,
)

__all__ = [
    "ALLOWED_LOOPBACK_HOSTS",
    "_assert_not_production",
    "_assert_loopback_url",
]
