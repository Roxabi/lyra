"""Type-aware JSON serializer for NatsBus wire format.

Handles encoding/decoding of Lyra dataclasses to/from UTF-8 JSON bytes:
- Enum  → .value (str/int)
- datetime → .isoformat()
- bytes → "b64:<base64>" prefixed string
- callables stripped from dict fields (platform_meta)
- nested dataclasses serialized recursively
"""

from __future__ import annotations

import base64
import dataclasses
import importlib
import json
import sys
import types
import typing
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar, get_type_hints

T = TypeVar("T")

_B64_PREFIX = "b64:"
_hints_cache: dict[type, dict[str, Any]] = {}

_TYPE_CHECKING_IMPORTS: list[tuple[str, str]] = []


def register_type_checking_import(module_path: str, type_name: str) -> None:
    """Register a TYPE_CHECKING-only type for runtime hint resolution.

    Some dataclasses reference types imported only under ``TYPE_CHECKING``
    (to avoid runtime circular imports). At deserialization time those names
    must be resolvable. Consumers that send such dataclasses over NATS can
    register them here once at startup; the serializer will attempt the
    import lazily and fall back silently if unavailable.

    Duplicate registrations are deduped. Registration after first
    deserialization of the affected type has no effect (hints are cached).
    """
    entry = (module_path, type_name)
    if entry not in _TYPE_CHECKING_IMPORTS:
        _TYPE_CHECKING_IMPORTS.append(entry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def serialize(item: Any) -> bytes:
    """Serialize a dataclass instance to UTF-8 JSON bytes.

    Encodes Enum → .value, datetime → .isoformat(), bytes → "b64:<base64>".
    Callables are stripped from any dict-typed field (e.g. platform_meta).
    """
    encoded = _encode(item)
    return json.dumps(encoded, ensure_ascii=False).encode("utf-8")


def deserialize(data: bytes, item_type: type[T]) -> T:
    """Reconstruct a dataclass from UTF-8 JSON bytes.

    Parses JSON, then recursively reconstructs item_type from the resulting
    dict, rehydrating enums, datetimes, bytes fields, and nested dataclasses.
    """
    raw = json.loads(data.decode("utf-8"))
    return _decode(raw, item_type)  # type: ignore[return-value]


def deserialize_dict(d: dict[str, Any], item_type: type[T]) -> T:
    """Reconstruct a dataclass from a pre-parsed dict.

    Same as :func:`deserialize` but skips the JSON parse step — use when
    the caller already has a ``dict`` (e.g. from a prior ``json.loads``).
    """
    return _decode(d, item_type)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_callables(d: dict[str, Any]) -> dict[str, Any]:
    """Remove callable values from a dict (used for platform_meta)."""
    return {k: v for k, v in d.items() if not callable(v)}


def _get_hints(dc_type: type) -> dict[str, Any]:
    """Get type hints for a dataclass, handling TYPE_CHECKING-only imports.

    Uses the class's own module globals as the resolution namespace so that
    names like ``Attachment`` always resolve to the correct class even when
    other packages (e.g. discord.py) define identically-named types.

    Falls back to explicitly importing known TYPE_CHECKING-only types when
    NameError is raised (e.g. ``CommandContext`` imported under TYPE_CHECKING).

    Results are cached per type to avoid repeated ``get_type_hints`` calls.
    """
    cached = _hints_cache.get(dc_type)
    if cached is not None:
        return cached

    try:
        result = get_type_hints(dc_type)
        _hints_cache[dc_type] = result
        return result
    except NameError:
        pass

    # Use the class's own module globals as the primary resolution namespace.
    # Do NOT scrape all of sys.modules — that causes name collisions when
    # third-party packages (e.g. discord.py) export identically-named types.
    module = sys.modules.get(dc_type.__module__)
    globalns: dict[str, Any] = dict(vars(module)) if module is not None else {}

    # Supplement with TYPE_CHECKING-only types not present at runtime.
    # The registry is populated by consumers via register_type_checking_import();
    # the SDK itself knows no domain types.
    localns: dict[str, Any] = {}
    for module_path, type_name in _TYPE_CHECKING_IMPORTS:
        if type_name not in globalns:
            try:
                mod = importlib.import_module(module_path)
                localns[type_name] = getattr(mod, type_name)
            except Exception:
                pass

    try:
        result = get_type_hints(dc_type, globalns=globalns, localns=localns)
    except Exception:
        # Final fallback: no type coercion — raw JSON values returned as-is.
        # Do NOT cache the empty fallback: a transient resolution failure
        # should not permanently disable type coercion for this type.
        return {}
    _hints_cache[dc_type] = result
    return result


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _encode(obj: Any) -> Any:
    """Recursively encode obj to a JSON-safe value.

    Handles: dataclass, Enum, datetime, bytes, list, dict, scalars.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {}
        for f in dataclasses.fields(obj):  # type: ignore[arg-type]
            value = getattr(obj, f.name)
            encoded_value = _encode(value)
            # Strip callables from dict fields (platform_meta pattern)
            if isinstance(encoded_value, dict):
                encoded_value = _strip_callables(encoded_value)
            result[f.name] = encoded_value
        return result

    if isinstance(obj, Enum):
        return obj.value

    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, bytes):
        return _B64_PREFIX + base64.b64encode(obj).decode("ascii")

    if isinstance(obj, list):
        return [_encode(item) for item in obj]

    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items() if not callable(v)}

    # Scalar: str, int, float, bool, None
    return obj


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_union(value: Any, args: tuple[Any, ...]) -> Any:
    """Decode value when the target is a Union / Optional type."""
    if value is None:
        return None
    non_none = [a for a in args if a is not type(None)]
    # bytes: detect "b64:" prefix
    if bytes in non_none and isinstance(value, str) and value.startswith(_B64_PREFIX):
        return base64.b64decode(value[len(_B64_PREFIX) :])
    # Single non-None candidate: decode as that type
    if len(non_none) == 1:
        return _decode(value, non_none[0])
    # Multiple non-None: str | bytes without b64 prefix → keep as str
    if str in non_none and isinstance(value, str):
        return value
    # Dataclass candidate: try to reconstruct
    for candidate in non_none:
        if (
            dataclasses.is_dataclass(candidate)
            and isinstance(candidate, type)
            and isinstance(value, dict)
        ):
            return _decode_dataclass(value, candidate)
    return value


def _decode_concrete(value: Any, target_type: Any) -> Any:
    """Decode value for concrete (non-generic, non-union) types."""
    # ── Dataclass ────────────────────────────────────────────────────────────
    if dataclasses.is_dataclass(target_type) and isinstance(target_type, type):
        if value is None:
            return None
        if not isinstance(value, dict):
            return value
        return _decode_dataclass(value, target_type)

    # ── Enum ──────────────────────────────────────────────────────────────────
    if isinstance(target_type, type) and issubclass(target_type, Enum):
        return target_type(value)

    # ── datetime ──────────────────────────────────────────────────────────────
    if target_type is datetime:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    # ── bytes ─────────────────────────────────────────────────────────────────
    if target_type is bytes:
        if isinstance(value, str) and value.startswith(_B64_PREFIX):
            return base64.b64decode(value[len(_B64_PREFIX) :])
        if isinstance(value, bytes):
            return value
        raise ValueError(
            f"Expected b64:-prefixed string for bytes field, got {type(value).__name__}"
        )

    # ── Scalar / dict / unknown ───────────────────────────────────────────────
    return value


def _decode(value: Any, target_type: Any) -> Any:
    """Reconstruct value as target_type.

    Handles: dataclass, Enum, datetime, bytes, list[X], Union/Optional, scalars.
    """
    if target_type is Any or target_type is None:
        return value

    origin = typing.get_origin(target_type)
    args = typing.get_args(target_type)

    # ── Union / Optional (including X | Y syntax) ────────────────────────────
    is_union = origin is typing.Union or (
        hasattr(types, "UnionType") and isinstance(target_type, types.UnionType)
    )
    if is_union:
        return _decode_union(value, args)

    # ── list[X] ──────────────────────────────────────────────────────────────
    if origin is list:
        if value is None:
            return value
        elem_type = args[0] if args else Any
        return [_decode(item, elem_type) for item in value]

    # ── Literal — return as-is ───────────────────────────────────────────────
    if origin is typing.Literal:
        return value

    return _decode_concrete(value, target_type)


def _decode_dataclass(d: dict[str, Any], dc_type: type) -> Any:
    """Reconstruct a dataclass from a dict using field type hints for coercion."""
    hints = _get_hints(dc_type)

    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(dc_type):  # type: ignore[arg-type]
        if f.name not in d:
            # Field absent in payload — omit; rely on default or default_factory
            continue
        raw_value = d[f.name]
        field_type = hints.get(f.name)
        if field_type is None:
            kwargs[f.name] = raw_value
        else:
            kwargs[f.name] = _decode(raw_value, field_type)

    return dc_type(**kwargs)
