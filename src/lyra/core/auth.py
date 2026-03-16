"""Backward-compat facade — use lyra.core.authenticator directly.

AuthMiddleware is an alias for Authenticator. All existing imports
(``from lyra.core.auth import AuthMiddleware``) continue to work.
"""

from lyra.core.authenticator import _ALLOW_ALL, _DENY_ALL  # noqa: F401
from lyra.core.authenticator import Authenticator as AuthMiddleware  # noqa: F401
from lyra.core.trust import TrustLevel  # noqa: F401 — backward compat

__all__ = ["AuthMiddleware", "TrustLevel", "_ALLOW_ALL", "_DENY_ALL"]
