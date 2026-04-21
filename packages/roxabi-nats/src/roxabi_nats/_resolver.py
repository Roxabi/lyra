"""`_TypeHintResolver` — per-instance registry of TYPE_CHECKING-only types.

Extracted from `_serialize.py` so that the serializer module stays within the
300-line cap and the resolver has a single-responsibility home.  The public
alias is re-exported from the package root as ``TypeHintResolver``
(see `__init__.py`).
"""

from __future__ import annotations

import importlib
import itertools
import types
from collections.abc import Sequence
from typing import Any

# Per-instance monotonic counter — used as part of the `_hints_cache` key in
# `_serialize._get_hints` so two resolvers never collide even if CPython
# recycles `id()` after GC.  Plain `itertools.count()` is thread-safe for the
# purpose of minting unique ints because of the GIL.
_uid_counter = itertools.count()


class _TypeHintResolver:
    """Per-instance registry of TYPE_CHECKING-only types for deserialization.

    Constructed at adapter/consumer init time. Eagerly imports every
    (module_path, type_name) entry and caches the resolved type object so the
    hot deserialization path never calls ``importlib``.  Non-existent
    modules or attributes raise ``ValueError`` immediately — fail-fast at
    construction, not on first message.

    Each instance carries a monotonic ``_uid`` that callers pair with the
    dataclass type to form the serializer's hint-cache key.  The UID is
    unforgeable and never reused, so GC of a resolver cannot produce a stale
    cache hit against a new resolver at the same ``id()`` address.
    """

    __slots__ = ("_uid", "entries", "resolved")

    def __init__(self, entries: Sequence[tuple[str, str]]) -> None:
        seen: set[tuple[str, str]] = set()
        deduped: list[tuple[str, str]] = []
        resolved: dict[str, type] = {}
        # name_to_module: type_name → module_path for the entry that first
        # claimed it. O(1) lookup for the duplicate-name error; keeps the
        # predicate unambiguous regardless of `seen`-set evolution.
        name_to_module: dict[str, str] = {}
        for module_path, type_name in entries:
            key = (module_path, type_name)
            if key in seen:
                continue
            seen.add(key)
            if type_name in resolved:
                raise ValueError(
                    f"type_registry: duplicate type_name {type_name!r} from "
                    f"{module_path!r} conflicts with earlier entry "
                    f"from {name_to_module[type_name]!r}"
                )
            try:
                mod = importlib.import_module(module_path)
            except ImportError as exc:
                raise ValueError(f"type_registry: cannot import {module_path}") from exc
            if not hasattr(mod, type_name):
                raise ValueError(
                    f"type_registry: {module_path} has no attribute {type_name}"
                )
            resolved[type_name] = getattr(mod, type_name)
            name_to_module[type_name] = module_path
            deduped.append(key)
        self.entries: tuple[tuple[str, str], ...] = tuple(deduped)
        self.resolved: types.MappingProxyType[str, type] = types.MappingProxyType(
            resolved
        )
        self._uid: int = next(_uid_counter)

    def localns(self) -> dict[str, Any]:
        return dict(self.resolved)


_EMPTY_RESOLVER = _TypeHintResolver(())
