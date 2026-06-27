## Type Safety Audit — Partition P07 (infrastructure / transport / nats)

**Date:** 2026-05-26
**Scope:** `src/lyra/infrastructure/**/*.py`, `src/lyra/transport/**/*.py`, `src/lyra/nats/**/*.py`
**Files:** 36
**Lines:** ~5,755

---

### Summary

- **Excellent return-type coverage:** 100 % of public functions have return-type hints (0 missing). No untyped `*args`/`**kwargs` and no `cast()` / `assert isinstance()` usage anywhere.
- **8 `# type: ignore` comments** — 5 are symptoms of contract-model nullability gaps (`SttResponse` / `TtsResponse` success-path fields typed as optional); 1 is a missing runtime guard (`self._sub` may be `None`); 1 is a Pydantic-Literal narrowing quirk; 1 is a `deserialize()` return-value mismatch that should use `cast()`.
- **10 bare `dict` annotations** in function signatures (returns + params) — all should be `dict[str, Any]` or a `TypedDict`.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/nats/nats_stt_codec.py` | 98–100 | Medium | 3× `# type: ignore[arg-type]` because `resp.text`, `resp.language`, `resp.duration_seconds` are `str \| None` / `float \| None` in `SttResponse` even though the `ok=True` branch guarantees non-None values. | Fix `SttResponse` Pydantic model: make success-path fields non-optional (or add a narrowed `@property` / `ValidatedSttResponse` alias). |
| `src/lyra/nats/nats_tts_codec.py` | 108–109 | Medium | 2× `# type: ignore[arg-type]` because `resp.mime_type` and `resp.duration_ms` are optional in `TtsResponse` despite `ok=True` branch. | Same as above — tighten `TtsResponse` model for the success path. |
| `src/lyra/infrastructure/turn_writer/writer.py` | 114 | Low | `# type: ignore[union-attr]` on `self._sub.fetch(...)`; `_sub` is `JetStreamContext.PullSubscription \| None`. | Add `assert self._sub is not None` inside `_consume_loop` (guaranteed by `start()` logic) and drop the ignore. |
| `src/lyra/nats/render_event_codec.py` | 119 | Low | `# type: ignore[return-value]` on `deserialize(...)` inside `_decode` closure. `deserialize` returns `object`; the registry guarantees the concrete type, but pyright/mypy cannot see it. | Replace with `cast(RenderEvent, deserialize(...))` to make the coercion explicit. |
| `src/lyra/transport/turn_publisher.py` | 94 | Low | `# type: ignore[arg-type]` passing `str` to a Pydantic `Literal` field. | Acceptable pattern, but document with a link to the upstream Pydantic issue or replace with a `typing.cast(Literal[...], role)` call. |
| `src/lyra/transport/worker_pool_client.py` | 39 | Low | `_RegistryLike.ordered_by_score() -> list[Any]` uses `Any` instead of a concrete worker-stats type. | Introduce `_WorkerInfo` TypedDict or alias and replace `list[Any]` with `list[_WorkerInfo]`. |
| `src/lyra/transport/worker_pool_client.py` | 100 | Low | `_on_heartbeat(self, msg: Any)` — NATS `Msg` type is available via `nats.aio.msg.Msg`. | Annotate as `nats.aio.msg.Msg` (or `nats.aio.msg.Msg \| object` if cross-version compatibility is a concern). |
| `src/lyra/infrastructure/stores/agent_store.py` | 110 | Low | `get_bot_settings(self, ...) -> dict` — bare generic. | Change to `dict[str, Any]`. |
| `src/lyra/infrastructure/stores/bot_agent_map.py` | 93 | Low | `get_bot_settings(self, ...) -> dict` — bare generic. | Change to `dict[str, Any]`. |
| `src/lyra/nats/tts_engine_selector.py` | 50 | Low | `build_generate_kwargs(...) -> dict` — bare generic. | Change to `dict[str, Any]`. |
| `src/lyra/infrastructure/stores/auth_store.py` | 158 | Low | `seed_from_config(self, raw: dict, ...)` — bare generic. | Change to `dict[str, Any]`. |
| `src/lyra/infrastructure/stores/turn_store.py` | 159 | Low | `_log_turn(..., metadata: dict \| None = None)` — bare generic. | Change to `dict[str, Any] \| None`. |
| `src/lyra/transport/turn_publisher.py` | 78 | Low | `publish_log_turn(..., metadata: dict \| None = None)` — bare generic. | Change to `dict[str, Any] \| None`. |
| `src/lyra/nats/worker_registry.py` | 64 | Low | `record_heartbeat(self, payload: dict) -> None` — bare generic. | Change to `dict[str, Any]`. |
| `src/lyra/transport/worker_pool_client.py` | 35 | Low | Protocol `record_heartbeat(self, payload: dict) -> None` — bare generic. | Change to `dict[str, Any]`. |

---

### Metrics

| Metric | Count | % of corpus |
|--------|-------|-------------|
| Total functions | ~250 | — |
| Public functions | ~174 | — |
| Public functions missing return type | **0** | **0 %** |
| `typing.Any` usages (meaningful) | 8 | 0.14 % of lines |
| `# type: ignore` comments | 8 | 0.14 % of lines |
| `cast()` usages | 0 | — |
| `assert isinstance()` usages | 0 | — |
| Untyped `*args` / `**kwargs` | 0 | — |
| Bare `dict` in signatures | 10 | — |
| Bare `list` in signatures | 0 | — |

---

### Recommendations (prioritized)

1. **Tighten `SttResponse` and `TtsResponse` contract models** — 5 `type: ignore` comments (the densest cluster) evaporate if success-path fields are non-optional. This is the highest-impact fix.
2. **Add runtime guard in `TurnWriter._consume_loop`** — Replace `type: ignore[union-attr]` with `assert self._sub is not None` before fetch. Safer and self-documenting.
3. **Replace `render_event_codec` `type: ignore[return-value]` with `cast()`** — Makes the registry-driven type coercion explicit rather than silencing the checker.
4. **Parameterize 10 bare `dict` signatures** — Batch change `dict` → `dict[str, Any]` (or `dict[str, object]` where stricter) across the 6 files listed above. Low risk, mechanical.
5. **Narrow `WorkerPoolClient` protocol types** — Replace `list[Any]` in `_RegistryLike.ordered_by_score` and `msg: Any` in `_on_heartbeat` with concrete NATS/structural types. Reduces the leak of `Any` into downstream call-sites.
