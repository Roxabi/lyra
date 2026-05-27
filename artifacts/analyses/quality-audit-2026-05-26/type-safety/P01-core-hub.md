### Summary

- **Very strong type coverage**: 3,909 LOC with only 2 `# type: ignore`, zero `cast()` / `assert isinstance()`, and minimal `Any` usage. The partition is among the best-typed in lyra.
- **Main gaps are structural, not accidental**: untyped command-router (`Any`), duck-typed TTS (`object | None`), and an untyped queue-item alias (`tuple`) are all conscious boundary-layer choices that could be narrowed with Protocols.
- **Single real suppression debt**: `middleware_stt.py` uses `hub: object` plus 2 `# type: ignore[union-attr]` — a one-file fix that removes 100 % of the partition's type-safety suppressions.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `middleware/middleware_stt.py` | 172 | Medium | `_dispatch_error` parameter typed as `hub: object` | Change to `Hub` with `TYPE_CHECKING` import; removes need for the 2 `type: ignore` below. |
| `middleware/middleware_stt.py` | 174 | Low | `# type: ignore[union-attr]` on `hub._msg_manager.get(key)` | Remove after narrowing `hub` to `Hub`. |
| `middleware/middleware_stt.py` | 175 | Low | `# type: ignore[union-attr]` on `hub.dispatch_response(...)` | Remove after narrowing `hub` to `Hub`. |
| `middleware/middleware.py` | 54 | Low | `PipelineContext.router: Any = None` | Introduce `CommandRouter` Protocol or `type: ignore` the one field; `Any` bleeds into every middleware stage. |
| `middleware/middleware_pool.py` | 183 | Low | `_dispatch_command` parameter `router: Any` | Same as above; once `CommandRouter` Protocol exists, replace both `router: Any` sites. |
| `outbound/outbound_errors.py` | 34 | Low | `_ITEM = tuple` — unparameterised alias for heterogeneous queue shapes | Replace with `typing.Union` of the 6 concrete `tuple[Literal["send"], ...]` shapes, or `tuple[object, ...]` as minimum. |
| `outbound/_dispatch.py` | 32–33 | Low | `verify_routing_fn` and `try_notify_fn` parameters have no type hints | Add `Callable[[RoutingContext \| None], bool]` and `Callable[[InboundMessage, str], Awaitable[None]]` respectively. |
| `outbound/outbound_errors.py` | 97 | Low | `try_notify_user` parameter `adapter: Any` | Use `ChannelAdapter` under `TYPE_CHECKING`; the hard-import avoidance rationale is weaker now that the file already imports `InboundMessage` from the same module. |
| `outbound/outbound_errors.py` | 101 | Low | `try_notify_user` parameter `circuit: Any = None` | Use `CircuitBreaker \| None` under `TYPE_CHECKING`; same rationale as above. |
| `outbound/outbound_router.py` | 62, 94 | Low | `tts: object \| None` used to avoid circular import | Keep or replace with `TtsProtocol` under `TYPE_CHECKING`; already done for `AudioPipeline` in the same file. |
| `outbound/outbound_tts.py` | 33, 44 | Low | `tts: object \| None` used to avoid circular import | Same as above — align with `AudioPipeline` pattern already used in `OutboundRouter.__init__`. |
| `hub_protocol.py` | 60, 63 | Info | `normalize(self, raw: Any)` and `normalize_audio(..., raw: Any)` | **Acceptable** — Protocol boundary for platform-specific raw payloads. Document as intentional. |

### Metrics

| Metric | Count | Notes |
|---|---|---|
| Total Python LOC | 3,909 | 31 files across `core/hub` |
| `typing.Any` imports | 7 files | |
| Semantic `Any` annotations | 6 | 2 × `raw: Any` (Protocol boundary), 2 × `router: Any`, 1 × `adapter: Any`, 1 × `circuit: Any` |
| Variadic `Any` in `Coroutine[Any, Any, None]` | 3 | `_dispatch.py` via `hub_dispatch.py`, `outbound_audio.py`, `outbound_router.py` — idiomatic for unknown coroutine args |
| `# type: ignore` | 2 | Both `[union-attr]` in `middleware_stt.py`; 0 with no reason |
| `# type: ignore` with documented reason | 2 | 100 % |
| `cast()` | 0 | |
| `assert isinstance()` | 0 | |
| `object` duck-typing annotations | 5 | 1 × `hub: object`, 4 × `tts: object \| None` |
| Missing return type on public functions | 0 | All public functions have `->` annotations |
| Untyped `**kwargs` / `*args` | 0 | `**payload: object` in `PipelineContext.trace` is typed |
| Untyped callable parameters | 2 | `verify_routing_fn`, `try_notify_fn` in `_dispatch.py` |
| `hasattr` / `getattr` duck-typing | 14 calls | 6 files; bypass static typing but are runtime-safe |
| Weak type aliases | 1 | `_ITEM = tuple` in `outbound_errors.py` |

### Recommendations (prioritized)

1. **Narrow `hub: object` → `Hub` in `middleware_stt.py`** (lines 172–175). This single change deletes the partition's only 2 `# type: ignore` suppressions. Import `Hub` under `TYPE_CHECKING`.

2. **Define `CommandRouter` Protocol and retire `router: Any`** in `middleware/middleware.py:54` and `middleware/middleware_pool.py:183`. The protocol only needs the 3 methods actually called (`is_command`, `dispatch`, `prepare`) — a ~6-line addition that removes the most pervasive `Any` in the partition.

3. **Type the `_dispatch.py` callback parameters** (`verify_routing_fn`, `try_notify_fn`). These are internal boundaries, but adding annotations prevents accidental signature drift when `OutboundDispatcher` evolves.

4. **Parameterise `_ITEM` in `outbound_errors.py`** as a `Union` of the 6 literal-shaped tuples. This improves readability and lets the type-checker validate `dispatch_outbound_item` tuple unpacking.

5. **Align TTS typing** in `outbound_router.py` and `outbound_tts.py` with the `AudioPipeline` pattern already used nearby: import `TtsProtocol` under `TYPE_CHECKING` and replace `object | None`. This removes the last `object` escape hatches.
