"""Brand slug helpers for social media provider groups."""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_brand(name: str) -> str:
    """Map a provider group display name to a Factory ``brand_slug``."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "unknown"


def resolve_group_id(groups: list[dict], brand_slug: str) -> str | None:
    for group in groups:
        name = group.get("name")
        if not isinstance(name, str):
            continue
        if slugify_brand(name) == brand_slug:
            group_id = group.get("id")
            return group_id if isinstance(group_id, str) else None
    return None