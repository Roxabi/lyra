### Summary

- One function exceeds 100 lines (`dispatch_outbound_item`, 167 LOC) and three functions carry explicit cognitive-complexity debt (C901/PLR0915 noqa); no 300-line file exemptions are violated.
- Three god classes persist despite prior mixin/helper extractions (#760): `Hub` (10+ wired subsystems), `OutboundRouter` (6 dispatch types), and `PoolManager` (6 lifecycle responsibilities).
- One DRY violation and three feature-envy sites remain residual: `TtsDispatch` repeats the same 6-line synthesis block in three methods, while `PoolManager`, `MessagePrepMiddleware`, and `HubRegistrationMixin` reach into peer private state.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `outbound/_dispatch.py` | 27 | High | `dispatch_outbound_item` is 167 LOC, handles 6 message kinds, per-kind routing extraction, circuit breaker, retry loop with backoff, callback invocation, and iterator draining. Explicit noqa: C901, PLR0913, PLR0915. | Replace the `_ITEM` tuple with polymorphic dataclass union (`SendItem`, `StreamingItem`, `AudioItem`, etc.) and dispatch via a registry or `match`/`case`; separate retry orchestrator from the per-kind send logic. |
| `middleware/middleware_stt.py` | 73 | Medium | `SttMiddleware.__call__` is 96 LOC with 6 inline outcome branches (success + unsupported/unavailable/noise/invalid/failed). Explicit noqa: C901, PLR0915. | Extract each outcome into a private `_handle_*` method; return a result type or small state machine instead of inline `return _DROP` repeated 5 times. |
| `outbound/outbound_streaming.py` | 46 | Medium | `StreamingDispatch.dispatch` is 90 LOC, mixing voice-tee setup, dispatcher-queue routing, direct-adapter fallback, and text-accumulation fallback. Explicit noqa: C901, PLR0915. | Extract the `send_streaming`-less adapter fallback path (text accumulation + `OutboundMessage.from_text`) into `StreamingDispatch._fallback_to_text()`. |
| `outbound/outbound_tts.py` | 62, 115, 153 | Medium | Same 6-line TTS synthesis block (`resolve_agent_tts` + `_resolve_agent_fallback_language` + `synthesize_and_dispatch_audio` with identical kwargs) repeated in `dispatch_tts_for_response`, `create_deferred_tts_task`, and `dispatch_tts_from_parts`. | Extract a private `_synthesize_and_dispatch(msg, text)` helper that encapsulates the boilerplate. |
| `hub.py` | 56 | Medium | `Hub` god class: inherits 5 mixins and `__init__` still wires 25+ attributes across 10 subsystems (bus, pools, dispatchers, registries, stores, TTS, STT, auth, rate limiter, outbound router). Explicit noqa: PLR0913. | Move `__init__` wiring into a builder or bootstrap factory so `Hub` remains a facade; constructor should receive pre-built subsystems rather than raw dependencies. |
| `pipeline/pool_manager.py` | 22 | Medium | `PoolManager` feature envy: reads 8 private attributes from `Hub` (`_max_pools`, `_pool_ttl`, `_turn_store`, `_turn_publisher`, `_message_index`, `_pairing_manager`, `agent_registry`, `cli_pool`). | Inject required dependencies explicitly via constructor instead of the whole `Hub` reference; pass registry, stores, and CLI pool as typed parameters. |
| `middleware/middleware_pool.py` | 68 | Medium | `MessagePrepMiddleware.__call__` reaches into `Pool` internals (`_on_resume_fn`, `_configured`, `_observer`) and `Hub` private stores (`_turn_publisher`, `_turn_store`). | Move resume-fn wiring and observer registration into `PoolManager.get_or_create_pool()` or a `Pool` factory method so the middleware sees only public pool state. |
| `hub/hub_registration.py` | 53 | Low | `register_agent` reaches into agent private fields (`_memory`, `_task_registry`, `command_router._on_debounce_change`, `command_router._on_cancel_change`). | Delegate agent wiring to a bootstrap helper or an agent-side `wire_to_hub()` method; Hub should not know agent internals. |
| `outbound/outbound_router.py` | 45 | Low | `OutboundRouter` coordinates 6 dispatch types plus TTS/audio helpers; responsibilities remain >5 despite prior extraction of `AudioDispatch`, `TtsDispatch`, and `StreamingDispatch`. | Extract a `ResponseDispatch` helper to own the non-streaming response path (callback unwrapping, audio follow-up, TTS trigger) and shrink `OutboundRouter` to pure routing. |
| `outbound/outbound_errors.py` | 34 | Low | `_ITEM = tuple` is a heterogeneous queue item shaped by convention; positional indexing (`item[0]`, `item[1]`) is fragile and repeated in `_dispatch.py`. | Replace `_ITEM` with a `@dataclass` union (e.g. `SendItem`, `StreamingItem`, `AudioItem`) and add a `kind: Literal[...]` discriminator. |

### Metrics

| Metric | Count | Notes |
|---|---|---|
| Files analyzed | 31 | `src/lyra/core/hub/**/*.py` |
| Lines of code | ~3,600 | Approximate total |
| Functions >100 lines | 1 | `dispatch_outbound_item` (167 LOC) — 3% of files |
| God classes (>5 responsibilities) | 3 | `Hub`, `OutboundRouter`, `PoolManager` |
| DRY violations (>3 lines, >=2 files) | 1 | `TtsDispatch` triplication; `_ITEM` tuple is structural duplication |
| Cognitive complexity >15 (or explicit C901 noqa) | 3 | `dispatch_outbound_item`, `SttMiddleware.__call__`, `StreamingDispatch.dispatch` |
| Feature envy sites | 3 | `PoolManager`→`Hub`, `MessagePrepMiddleware`→`Pool`/`Hub`, `HubRegistrationMixin`→`Agent` |

### Recommendations (prioritized)

1. **Split `dispatch_outbound_item` into per-kind dispatchers**
   Highest impact. The 167-line function in `outbound/_dispatch.py` is the single largest complexity hotspot in the partition. Replace the `_ITEM` tuple with a sealed dataclass union and route dispatch through a small registry or `match` block. This simultaneously fixes the primitive-obsession smell and makes retry logic testable in isolation.

2. **Extract `TtsDispatch._synthesize_and_dispatch` helper**
   Quick win. The same 6-line block appears in three methods (`dispatch_tts_for_response`, `create_deferred_tts_task`, `dispatch_tts_from_parts`). A single private helper removes drift risk and halves the surface area of `TtsDispatch`.

3. **Inject explicit dependencies into `PoolManager` instead of `Hub`**
   `PoolManager` currently digs into 8 private `Hub` attributes. Passing `registry`, `stores`, `cli_pool`, and `config` explicitly breaks the tight coupling, improves unit-testability, and removes the feature-envy anti-pattern.

4. **Decompose `SttMiddleware.__call__` into private outcome handlers**
   The 96-line method handles 6 STT outcomes inline. Extract `_handle_stt_unavailable`, `_handle_stt_noise`, `_handle_stt_invalid`, `_handle_stt_failed`, `_handle_stt_success`, and `_handle_stt_unsupported` to reduce cognitive complexity from ~20 to ~5 per method.

5. **Move Hub `__init__` wiring to a builder or bootstrap factory**
   `Hub.__init__` is a 79-line wiring monolith with 13 parameters. Delegate construction to a `HubBuilder` or `_bootstrap_hub_standalone` so `Hub` becomes a pure facade. This addresses the residual god-class construction burden left after the #760 mixin extractions.
