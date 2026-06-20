"""Shared NATS KV key sanitization helpers.

NATS-py enforces ``VALID_KEY_RE = re.compile(r"^[-/_=\\.a-zA-Z0-9]+$")`` on every
KV key (see ``nats.js.kv``).  ``RoutingKey.to_pool_id()`` returns values like
``"telegram:lyra:7377831990"`` — the colon is rejected.  All KV stores MUST
sanitize key parts through ``kv_safe_part`` before interpolating them.
"""

from __future__ import annotations

import re

_UNSAFE_RE = re.compile(r"[^-/_=.A-Za-z0-9]")


def kv_safe_part(value: str) -> str:
    """Replace any character outside the NATS KV valid set with ``_``.

    NATS valid set: ``[-/_=.A-Za-z0-9]`` (mirrors ``nats.js.kv.VALID_KEY_RE``).
    Colons, spaces, ``*``, ``>`` and any other out-of-set chars become ``_``.

    Example::

        kv_safe_part("telegram:lyra:7377831990")
        # → "telegram_lyra_7377831990"
    """
    return _UNSAFE_RE.sub("_", value)
