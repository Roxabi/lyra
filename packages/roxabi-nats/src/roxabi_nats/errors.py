"""Wire-boundary error sanitization for socket-bound daemon paths.

The canonical structured error path on NATS reply subjects is
``WorkerError`` from ``roxabi_contracts.errors``
(ADR-066 (absorbed into ADR-049)). Its field
validators scrub credentials from embedded URLs and truncate free-text
fields to bounded length.

``sanitize_for_wire`` is the **free-function** counterpart for callers
that cannot return a typed envelope — e.g. legacy daemon error frames
written to a raw socket, or fallback paths inside an ``except`` handler
where constructing a Pydantic model would itself fail.

It reuses the same primitives (``scrub_credentials`` +
``truncate_with_marker``) so producer and consumer share a single
sanitization contract.
"""

from __future__ import annotations

from roxabi_contracts.errors import scrub_credentials, truncate_with_marker

__all__ = ["sanitize_for_wire", "DEFAULT_MAX_LEN"]

DEFAULT_MAX_LEN = 200


def sanitize_for_wire(exc: BaseException, *, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Return a wire-safe string representation of ``exc``.

    Pipeline: ``str(exc)`` → ``scrub_credentials`` (strips userinfo from
    embedded URLs of known credential-bearing schemes: ``nats``,
    ``postgres``, ``redis``, ``http``, etc.) → ``truncate_with_marker``
    (caps at ``max_len`` chars, replacing the tail with ``…`` on overflow).

    ``max_len`` must be ≥ 1 (the truncation marker length). Smaller values
    cannot produce a bounded string and raise ``ValueError``.

    Prefer ``WorkerError(code=..., message=str(exc))`` on NATS reply
    subjects — it applies the same sanitization via field validators
    inside a typed envelope the consumer can dispatch on.
    """
    if max_len < 1:
        raise ValueError(f"max_len must be >= 1, got {max_len}")
    return truncate_with_marker(scrub_credentials(str(exc)), max_len)
