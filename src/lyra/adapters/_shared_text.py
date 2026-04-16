"""Text utility functions for channel adapters.

Extracted from _shared.py to keep file sizes manageable.
"""

from __future__ import annotations

import os
import re
from typing import Callable

__all__ = [
    "truncate_caption",
    "sanitize_filename",
    "chunk_text",
]


def truncate_caption(caption: str | None, limit: int) -> str | None:
    """Truncate caption to *limit* characters, returning None if empty."""
    if not caption:
        return None
    return caption[:limit]


def sanitize_filename(
    filename: str,
    allowed_exts: frozenset[str],
    fallback: str = "attachment.bin",
) -> str:
    """Sanitize a caller-supplied filename for outbound attachments.

    Strips path components, control characters, and validates the
    extension against *allowed_exts*. Returns *fallback* if the
    result is empty or the extension is not whitelisted.
    """
    # Strip path components (defense against ../../ traversal)
    name = os.path.basename(filename)
    # Strip control characters and null bytes
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Enforce length cap
    name = name[:255]

    if not name:
        return fallback

    # Validate extension against whitelist
    _, ext = os.path.splitext(name)
    ext_clean = ext.lstrip(".").lower()
    if ext_clean not in allowed_exts:
        return fallback

    return name


def chunk_text(
    text: str,
    max_len: int,
    escape_fn: Callable[[str], str] | None = None,
) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters.

    Splits at the latest natural boundary before the limit (in order of
    preference): paragraph break, newline, sentence end (`. ` / `! ` / `? `),
    word boundary.  Falls back to a hard cut only if no boundary is found.

    If *escape_fn* is provided it is applied to the entire text before
    chunking (e.g. MarkdownV2 escaping), so *max_len* applies to the
    post-escape length. Callers must account for any expansion the escape
    function introduces. Returns [] for empty text.

    Raises ValueError if *max_len* is not positive.
    """
    if max_len <= 0:
        raise ValueError(f"chunk_text: max_len must be > 0, got {max_len!r}")
    if escape_fn is not None:
        text = escape_fn(text)
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while len(text) > max_len:
        window = text[:max_len]
        # Priority order: paragraph > newline > sentence end > word boundary
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            if idx > 0:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = max_len  # hard cut as last resort
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks
