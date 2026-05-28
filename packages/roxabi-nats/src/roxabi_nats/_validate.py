"""Shared validation helpers for NATS identifiers (subjects, queue groups, ids)."""

from __future__ import annotations

import re

_NATS_IDENT = re.compile(r"[A-Za-z0-9_.\-]+")
_NATS_SINGLE_TOKEN = re.compile(r"[A-Za-z0-9_\-]+")


def validate_nats_token(value: str, *, kind: str, allow_empty: bool = False) -> None:
    """Raise ``ValueError`` if *value* is not a valid NATS identifier token.

    A valid token matches ``[A-Za-z0-9_.\\-]+`` — no wildcards, spaces, or
    empty strings. Used for subject prefixes, queue group names, and similar
    identifiers that are interpolated into NATS protocol lines.

    Args:
        value: The token to validate.
        kind: Human-readable name used in the error message (e.g. ``"queue_group"``).
        allow_empty: When ``True``, an empty string is accepted (used for
            optional tokens like ``queue_group`` where empty means "no group").

    Raises:
        ValueError: If *value* does not match the NATS identifier pattern.
    """
    if allow_empty and not value:
        return
    if not _NATS_IDENT.fullmatch(value):
        raise ValueError(
            f"Invalid {kind} for NATS: {value!r} — "
            "must match [A-Za-z0-9_.\\-]+ (no wildcards or spaces)"
        )


def validate_nats_single_token(
    value: str, *, kind: str, allow_empty: bool = False
) -> None:
    """Raise ``ValueError`` if *value* is not a valid single NATS token.

    Unlike ``validate_nats_token``, this rejects dots (``.``) — the NATS
    token separator — so it is safe for fields like ``bot_id`` or
    ``worker_id`` that must never span multiple subject segments.

    A valid single token matches ``[A-Za-z0-9_\\-]+``.

    Args:
        value: The token to validate.
        kind: Human-readable name used in the error message (e.g. ``"bot_id"``).
        allow_empty: When ``True``, an empty string is accepted.

    Raises:
        ValueError: If *value* does not match the single-token pattern.
    """
    if allow_empty and not value:
        return
    if not _NATS_SINGLE_TOKEN.fullmatch(value):
        raise ValueError(
            f"Invalid {kind} for NATS: {value!r} — "
            "must match [A-Za-z0-9_\\-]+ (no dots, wildcards, or spaces)"
        )
