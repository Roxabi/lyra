# Type-Safety Audit — P10: packages/**/*.py

**Date:** 2026-05-27
**Scope:** roxabi-blobs, roxabi-contracts, roxabi-nats source (excl. tests/scripts)
**Lines:** ~5,296 total across packages
**Context:** Epic #1277 stage-axis refactor active; first full type-safety review of these packages.

---

## Summary

- **Return-type coverage is excellent:** 73/73 public functions in source files have return type hints (100%).
- **The risk is concentrated in two files:** `roxabi_nats/_serialize.py` holds 59% of all `Any` usages (34/58) and both `# type: ignore[return-value]` comments; `roxabi_nats/readiness.py` holds all 5 `# type: ignore[union-attr]` comments.
- **No untyped `*args`/`**kwargs` and no `assert isinstance()` in source.** One `cast()` exists and is avoidable with a runtime guard.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `roxabi_nats/_serialize.py` | 42, 147, 162, 215, 217, 236, 267, 277, 288, 318, 350 | High | 34 `typing.Any` usages (59% of package total). Core duck-typed encode/decode engine uses `Any` for every value in the recursive serialization path. | Acceptable for a generic serializer, but consider adding `TypeGuard` helpers or `@overload` sets for the common concrete types (dataclass, Pydantic, Enum, bytes) to reduce `Any` surface. |
| `roxabi_nats/_serialize.py` | 66, 80 | Medium | `# type: ignore[return-value]` on `deserialize` and `deserialize_dict`. `_decode` returns `Any`; wrapper promises `T`. | Replace ignore with `cast(T, _decode(...))` so the unsafety is explicit and grep-able. |
| `roxabi_nats/readiness.py` | 51, 56, 61, 157 | Medium | `# type: ignore[union-attr]` on `js.key_value()` / `js.create_key_value()`. `js` is typed as `object` to avoid importing nats-py JetStream types at runtime. | Define a minimal `Protocol` with `key_value` / `create_key_value` methods, or import `JetStream` under `TYPE_CHECKING` and type `js` as `JetStream`. |
| `roxabi_nats/readiness.py` | 105 | Low | `# type: ignore[no-untyped-def]` on nested `_handler(msg)`. Parameter `msg` is untyped. | Annotate `msg: nats.aio.msg.Msg` (import under `TYPE_CHECKING`). |
| `roxabi_nats/adapter_base.py` | 255 | Low | `cast(str, self._heartbeat_subject)` where attribute is `str \| None`. | Replace cast with an explicit runtime guard: `if self._heartbeat_subject is None: raise RuntimeError(...)` before use. |
| `roxabi_nats/driver_base.py` | 82, 112, 171 | Low | 3 `Any` usages: `_on_heartbeat(msg: Any)`, `_on_msg(msg: Any)`, `_wait_for_chunk` return `Any \| None`. | Type `msg` as `nats.aio.msg.Msg` (import under `TYPE_CHECKING`). Return type of `_wait_for_chunk` is already narrowed by caller. |
| `roxabi_nats/_resolver.py` | 77 | Low | `localns() -> dict[str, Any]` — `self.resolved` is `MappingProxyType[str, type]`. | Narrow return type to `dict[str, type]`. |
| `roxabi_nats/_sanitize.py` | 44, 47, 71 | Low | 3 `Any` usages in `sanitize_platform_meta`. Input `meta` and output are `dict[str, Any]` despite explicit scalar enforcement. | Narrow return type to `dict[str, str \| int \| bool]` after `_cap_value` guarantees scalar output. |
| `roxabi_nats/connect.py` | 149, 176 | Low | 2 `Any` usages: `**extra: Any` passthrough to `nats.connect()`, `kwargs: dict[str, Any]`. | Legitimate passthrough; keep as-is but document that `extra` is forwarded verbatim to nats-py. |
| `roxabi-contracts/jobs/models.py` | 26, 60, 86 | Low | 3 `Any` in `payload`, `data`, `detail` fields. | Intentional for JSON-shaped contract data; no action. |
| `roxabi-contracts/gh/fixtures.py` | 8, 14 | Low | 2 `Any` in test fixture dict constants. | Fixtures are literal dicts; acceptable. |
| `roxabi-contracts/llm/builders.py` | 15, 50 | Low | 2 `Any` in `payload: dict[str, Any]` builder parameter. | Builder accepts arbitrary inbound payload; acceptable. |
| `roxabi-contracts/voice/builders.py` | 21, 69 | Low | 2 `Any` in `payload: dict[str, Any]` builder parameter. | Builder accepts arbitrary inbound payload; acceptable. |
| `roxabi-contracts/jobs/fixtures.py` | 6–39 | Low | 5 `Any` in fixture dict constants. | Fixtures are literal dicts; acceptable. |
| `roxabi-blobs/http_store.py` | 37 | Low | 1 `Any` in `_start_asgi_lifespan(app: Any)` — test seam only. | Acceptable for ASGI app duck-typing in test code path. |

---

## Metrics

| Metric | Count | Note |
|--------|-------|------|
| Source files analyzed | 52 | Excluding tests, scripts, `__pycache__` |
| Total source LOC | ~2,400 | Tests + scripts excluded |
| Public functions | 73 | `def` / `async def` not starting with `_` |
| Functions with return types | 73 | **100% coverage** |
| `typing.Any` usages | 58 | 34 in `_serialize.py` alone |
| `# type: ignore` | 7 | 2 `[return-value]`, 5 `[union-attr]`, 1 `[no-untyped-def]` |
| `cast()` | 1 | `adapter_base.py` |
| `assert isinstance()` | 0 | — |
| Untyped `*args` / `**kwargs` | 0 | `connect.py` `**extra: Any` is typed |

---

## Recommendations (Prioritized)

1. **Type `js` in `readiness.py` with a Protocol or `TYPE_CHECKING` import** — eliminates all 5 `# type: ignore[union-attr]` comments and makes the JetStream boundary explicit.
2. **Replace `# type: ignore[return-value]` in `_serialize.py` with `cast(T, ...)`** — makes the type-system bypass visible and searchable; avoids silent ignores.
3. **Add runtime guard + remove `cast()` in `adapter_base.py` `_heartbeat_loop`** — `cast()` without a guard is a runtime blind spot; an `assert` or `if ... is None: raise` is safer.
4. **Narrow `driver_base.py` and `readiness.py` message handlers to `nats.aio.msg.Msg`** — imports under `TYPE_CHECKING` only; removes 3 `Any` usages with zero runtime cost.
5. **Narrow `_resolver.localns()` and `_sanitize.sanitize_platform_meta()` return types** — both have explicit runtime guarantees that are wider than their type signatures; tightening them removes 4 `Any` usages and improves downstream inference.
