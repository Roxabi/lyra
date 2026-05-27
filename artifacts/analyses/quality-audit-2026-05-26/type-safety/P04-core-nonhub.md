# P04 Type-Safety Audit — `src/lyra/core/**/*.py` (excl. hub/)

**Date:** 2026-05-26
**Scope:** 99 files, 282 public functions
**Context:** Epic #1277 stage-axis refactor; prior audit 2026-05-18 covered hexagonal conformance, duplication, dead-code. Those findings are not repeated unless regressed.

---

### Summary

- **Return-type coverage is 99.6 %** (1 missing out of 282 public functions). The single gap is in `tts_dispatch.py`.
- **All 7 `# type: ignore` comments carry explicit `DEBT:*` rationales** — none are unexplained. Five cluster in pool-processor streaming/exec around defensive narrowing of `object | None` parameters.
- **`cast()` and `assert isinstance()` are localized**: 8 casts (5 are mixin `self` casts, 2 are dynamic plugin handler casts, 1 is a log-record cast); 4 assertions narrow CLI entry types. No blind casts are used outside well-scoped structural patterns.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `src/lyra/core/tts_dispatch.py` | 164 | Medium | `_resolve_pool` lacks return type (`Pool \| None` inferred at runtime). | Add `-> Pool \| None`. |
| `src/lyra/core/config.py` | 81 | Medium | `CommandRouterConfig.session_driver: Any = None` erases the driver interface. | Introduce a `SessionDriver` protocol or `Callable[..., None] \| None`; replace `Any`. |
| `src/lyra/core/commands/command_loader.py` | 178, 229 | Medium | `cast("AsyncHandler", fn)` on dynamically loaded plugin handlers is a blind cast with no runtime guard. | Add a runtime `callable` + signature guard, or use `TypeIs[AsyncHandler]`; avoid silent mismatch at load time. |
| `src/lyra/core/pool/pool_processor_streaming.py` | 46, 48, 106, 112 | Low | Four `# type: ignore[misc]` (tagged `DEBT:defensive-narrow-payloads`) for `object \| None` params (`stream_done_event`, `processor`). | Narrow signatures to `asyncio.Event \| None` and `Processor \| None` (or a protocol) so the ignores can be deleted. |
| `src/lyra/core/pool/pool_processor_exec.py` | 168 | Low | `# type: ignore[misc]` (tagged `DEBT:defensive-narrow-payloads`) awaiting a union of coroutine and iterator. | Same as above — tighten the upstream type so `await` is statically safe. |
| `src/lyra/core/agent/agent_db_loader.py` | 64 | Low | `# type: ignore[arg-type]` passing `row.effort: str \| None` into `ModelConfig.effort: Literal[...] \| None`. | Replace ignore with an explicit `cast` or a tiny validation helper that asserts the literal values, making the trust boundary visible. |
| `src/lyra/core/messaging/callbacks.py` | 31 | Low | `# type: ignore[return-value]` (tagged `DEBT:lint-residual`) in `TrustedCallback.__call__`. | Return `T \| Awaitable[T]` from the helper and resolve the async branch properly, or use `typing.overload` on `__call__`. |
| `src/lyra/core/cli/cli_pool_lifecycle.py` | 34, 89 | Low | `cast(_CliPoolCore, self)` in mixin methods to access core methods. | Low risk — structural Python mixin pattern; acceptable if documented. Consider `Protocol` for `_CliPoolCore` to make the cast redundant. |
| `src/lyra/core/cli/cli_pool_streaming.py` | 57, 104, 136 | Low | Same mixin `self` casts as above. | Same recommendation. |
| `src/lyra/core/trace.py` | 91 | Low | `cast(TraceLogRecord, record)` inside `TraceIdFilter.filter`. | Low risk — `TraceLogRecord` is a runtime subclass of `logging.LogRecord`; the cast is safe but could be replaced by a `isinstance` guard + `assert`. |
| `src/lyra/core/cli/cli_non_streaming.py` | 31, 68 | Low | `assert isinstance(entry, _ProcessEntry)` at CLI entry points. | Acceptable defensive narrowing; consider replacing with `TypeGuard` / `TypeIs` if the check is reused elsewhere. |
| `src/lyra/core/cli/cli_streaming.py` | 41, 191 | Low | Same `assert isinstance(entry, _ProcessEntry)` pattern. | Same recommendation. |
| `src/lyra/core/messaging/message.py` | 146, 269 | Low | `metadata: dict[str, Any]` on `Response` and `OutboundMessage`. | Acceptable — metadata is a cross-boundary bag-of-values dict; narrowing to `str \| int \| bool` would break legitimate callback transport. |
| `src/lyra/core/messaging/events.py` | 60 | Low | `input: dict[str, Any]` on `ToolUseLlmEvent`. | Acceptable — LLM tool-use input shape is vendor-specific JSON. |
| `src/lyra/core/ports/llm.py` | 47 | Low | `capabilities: dict[str, Any]` in `LlmProvider` protocol. | Acceptable — capability dictionaries are vendor-extensible by design. |
| `src/lyra/core/agent/agent_refiner.py` | 120, 128, 218 | Low | `list[dict[str, Any]]` for LLM message lists. | Acceptable — standard OpenAI/Anthropic message shape; introducing a `MessageDict` TypedDict is low ROI unless used elsewhere. |

---

### Metrics

| Metric | Count | Note |
|--------|-------|------|
| Files analyzed | 99 | Excluding `hub/` |
| Public functions | 282 | `def [a-z_]\w*\(` excluding `__dunder__` |
| Missing return types | 1 | 99.6 % coverage |
| `typing.Any` usages | 21 | 2 in Pydantic validators (legitimate), 4 in `dict[str, Any]` metadata/LLM shapes, 1 in `session_driver` (should be narrowed), 2 in `tuple[Any, ...]` DB rows, 1 in generic callback wrapper, 1 in `**data: Any` Pydantic `__init__`, 2 in `Coroutine[Any, Any, None]`, 2 in `list[dict[str, Any]]`, 5 misc |
| `# type: ignore` | 7 | All tagged with `DEBT:*` rationale |
| `# type: ignore[misc]` | 5 | Defensive narrowing in pool processors |
| `# type: ignore[arg-type]` | 1 | `agent_db_loader` effort literal |
| `# type: ignore[return-value]` | 1 | `TrustedCallback` async/sync union |
| `cast()` | 8 | 5 mixin `self`, 2 plugin handler, 1 log record |
| `assert isinstance()` | 4 | CLI entry-point narrowing |
| Untyped `*args` / `**kwargs` | 1 | `TrustedCallback.__call__` (intentionally generic) |

---

### Recommendations (prioritized)

1. **Add return type to `tts_dispatch.py:164`** (`_resolve_pool`) — one-line fix that brings P04 to 100 % return-type coverage.
2. **Replace `session_driver: Any` with a `SessionDriver` protocol** in `CommandRouterConfig`. The `Any` erases all downstream type checking for a key bootstrap dependency.
3. **Narrow `object | None` parameters in pool processor streaming/exec** to their actual runtime types (`asyncio.Event`, `Processor`, `Callable[[], Awaitable[None]]`). This removes all 5 `DEBT:defensive-narrow-payloads` `type: ignore` comments in one sweep.
4. **Guard plugin handler casts in `command_loader.py`** with a runtime `inspect.iscoroutinefunction` or `callable` check, then use `TypeIs[AsyncHandler]` or an assertion. A blind `cast` can mask a broken plugin manifest at load time.
5. **Coerce `row.effort` explicitly before passing to `ModelConfig`** in `agent_db_loader.py` (e.g., `cast(Literal["low", "medium", "high", "xhigh", "max"], row.effort)`). This makes the DB→model trust boundary visible and removes the last `type: ignore[arg-type]`.
