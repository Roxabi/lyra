### Summary
- P08 is the cleanest partition audited so far: only 3 missing return types, 4 `Any` usages, and 4 `# type: ignore` comments across 26 files.
- The `**kwargs: Any` pattern in the `LlmCodec` protocol and its two implementations is the dominant type-safety gap; it masks the actual narrow API (`pool_id`, `lyra_session_id`).
- Three `# type: ignore[attr-defined]` comments in `llm_client.py` point to a structural typing debt: `WorkerPoolClient` does not expose `_transport` on its public interface.

### Findings
| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/agents/simple_agent.py` | 122 | Low | `_register_session_commands` missing return type `-> None` | Add `-> None` |
| `src/lyra/agents/simple_agent.py` | 158 | Low | `_maybe_register_reset` missing return type `-> None` | Add `-> None` |
| `src/lyra/agents/simple_agent.py` | 175 | Low | `_maybe_register_resume` missing return type `-> None` | Add `-> None` |
| `src/lyra/agents/simple_agent.py` | 302 | Low | `meta: dict[str, Any]` — value is always `str`; `Any` is unnecessary | Narrow to `dict[str, object]` or `dict[str, str \| bool]` |
| `src/lyra/llm/codec.py` | 28 | Low | Protocol `encode` uses `**kwargs: Any` | Replace with `*, pool_id: str = "", lyra_session_id: str \| None = None` or a `TypedDict` |
| `src/lyra/llm/cli_pool_codec.py` | 45 | Low | `encode` uses `**kwargs: Any`; only `pool_id` and `lyra_session_id` consumed | Narrow kwargs to explicit named parameters (match protocol) |
| `src/lyra/llm/cli_nats_codec.py` | 68 | Low | `encode` uses `**kwargs: Any`; same narrow usage as CliPoolCodec | Narrow kwargs to explicit named parameters (match protocol) |
| `src/lyra/llm/llm_client.py` | 156 | Medium | `# type: ignore[attr-defined]` — accesses private `_pool._transport` | Add `_transport` to `WorkerPoolClient` public Protocol, or expose a `call()` wrapper on the pool |
| `src/lyra/llm/llm_client.py` | 186 | Medium | `# type: ignore[attr-defined]` — same `_pool._transport` access | Same as above |
| `src/lyra/llm/llm_client.py` | 209 | Medium | `# type: ignore[attr-defined]` — same `_pool._transport` access | Same as above |
| `src/lyra/llm/cli_pool_codec.py` | 54 | Low | `# type: ignore[assignment]` — duck-types `ModelConfig` vs `dict` via `hasattr(model_cfg, "model_dump")` | Add a `ModelConfig` Protocol with `model_dump()`, or use `isinstance(model_cfg, BaseModel)` |
| `src/lyra/agent_cmd/agents/edit_cmd.py` | 97 | Low | `new_vals: dict = {}` — missing key/value type parameters | Annotate as `dict[str, object]` |
| `src/lyra/llm/decorators.py` | 34 | Low | `self.capabilities: dict = inner.capabilities` — missing type parameters | Annotate as `dict[str, object]` |
| `src/lyra/llm/drivers/cli.py` | 24 | Low | `capabilities: dict = {"streaming": True, ...}` — missing type parameters | Annotate as `dict[str, object]` |
| `src/lyra/llm/llm_client.py` | 48 | Low | `capabilities = {"streaming": True, ...}` — class attr without annotation | Annotate as `dict[str, object]` |
| `src/lyra/llm/cli_nats_codec.py` | 48 | Low | `assert code in KNOWN_CODES` — will crash on unknown worker codes instead of graceful degradation | Replace with `if code not in KNOWN_CODES: raise ValueError(...)` |

### Metrics
| Metric | Count |
|--------|-------|
| Files scanned | 26 |
| `typing.Any` usages | 4 |
| `# type: ignore` comments | 4 |
| Missing return types (public/internal) | 3 |
| Untyped `**kwargs` / `*args` | 3 |
| `cast()` calls | 0 |
| `assert isinstance()` | 0 |
| `assert` used as guard (non-isinstance) | 3 |
| Untyped `dict` / `list` locals | 1 |
| Return-type coverage (public functions) | ~95% (3 missing / ~60 public functions) |

### Recommendations
1. **Narrow the `LlmCodec` protocol kwargs** — Replace `**kwargs: Any` with explicit named parameters (`pool_id: str`, `lyra_session_id: str | None`) in both the Protocol and its two implementations. This eliminates 3 `Any` usages and makes the call sites self-documenting.
2. **Surface `_transport` on `WorkerPoolClient` or add a `call` wrapper** — The 3 `# type: ignore[attr-defined]` comments in `LlmClient` all access `self._pool._transport.call(...)`. Either expose `call(subject, payload, timeout)` directly on `WorkerPoolClient`, or add a `transport` property with a narrow Protocol so the type checker can validate it.
3. **Backfill the 3 missing return types in `SimpleAgent`** — `_register_session_commands`, `_maybe_register_reset`, and `_maybe_register_resume` should all declare `-> None`. This is a 30-second fix.
4. **Replace the `assert code in KNOWN_CODES` guard with a `ValueError`** — In `cli_nats_codec.py:48`, an unknown worker error code triggers a hard `AssertionError`. This should degrade gracefully (raise `ValueError`) because worker-controlled strings are a trust boundary.
5. **Add `dict[str, object]` annotations to 4 untyped capability / local dicts** — `LlmClient.capabilities`, `RetryDecorator.capabilities`, `CircuitBreakerDecorator.capabilities`, `ClaudeCliDriver.capabilities`, and `edit_cmd.py:new_vals` are all missing inner type parameters. A single pass with `dict[str, object]` improves pyright strict-mode compliance.
