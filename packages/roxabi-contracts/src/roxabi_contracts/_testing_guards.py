"""Backward-compat re-export — testing guards migrated to roxabi_nats.testing._guards.

ADR-059 V6: canonical location is now ``roxabi_nats.testing._guards``.
"""

import warnings

from roxabi_nats.testing._guards import (  # noqa: F401
    ALLOWED_LOOPBACK_HOSTS,
    assert_loopback_url,
    assert_not_production,
)

warnings.warn(
    "roxabi_contracts._testing_guards is deprecated; "
    "import from roxabi_nats.testing._guards instead (ADR-059 V6)",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ALLOWED_LOOPBACK_HOSTS",
    "assert_not_production",
    "assert_loopback_url",
]
