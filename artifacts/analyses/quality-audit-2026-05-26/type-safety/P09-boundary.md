### Summary

- P09 boundary layer is well-typed overall: 24 files scanned, **zero public functions missing return type hints**, and only 3 `# type: ignore` comments (all localized to `blobstore/`).
- Type-safety debt concentrates in `obs/` (12 `Any` usages in Protocol + NoOp mirror) and `tools/gh_token/helper.py` (3 `Any` usages for JSON shapes).
- No `cast()` found; one safe `assert isinstance()` in `helper.py` for PEM key narrowing.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/obs/base.py` | 22, 31 | info | `metadata: dict[str, Any]` in `ObsTrace` and `ObsSpan` dataclasses | Acceptable for opaque backend metadata; narrow to `dict[str, object]` or a TypedDict if schema stabilizes post-#1235 |
| `src/lyra/obs/base.py` | 72, 85, 110 | low | Protocol stubs use `**kwargs: Any` for `start_trace`, `start_span`, `record_event` | Define `TraceKwargs` / `EventKwargs` TypedDicts once the OTel/Langfuse backend shapes are known (#1235) |
| `src/lyra/obs/noop.py` | 14, 29, 62 | low | NoOp mirrors the Protocol's `**kwargs: Any` | Fix the Protocol first; NoOp will follow automatically |
| `src/lyra/obs/base.py` | 108-109 | low | `record_event` Protocol uses `input_data: Any = None`, `output_data: Any = None` | Document the PII contract in docstring (already present); if payloads have a known shape, replace with `Mapping[str, object] \| None` |
| `src/lyra/tools/gh_token/helper.py` | 101, 147, 243 | low | `dict[str, Any]` for JWT claims, cache JSON read, and GitHub API response body | Introduce small TypedDicts: `JWTClaims`, `TokenCacheData`, `GitHubTokenResponse` — schemas are stable and documented |
| `src/lyra/blobstore/auth.py` | 47 | low | `_op_from_request` returns `str`, but `BlobAuditEvent.op` expects a `Literal`; `# type: ignore[arg-type]` | Change `_METHOD_TO_OP` annotation to `dict[str, Literal["put", "get", "exists", "delete"]]` — the ignore becomes unnecessary |
| `src/lyra/blobstore/serve.py` | 104 | low | `_make_lifespan` missing return type; `# type: ignore[return]` | Add return type `-> Callable[[FastAPI], AsyncContextManager[None]]` (or a named Protocol) and remove the ignore |
| `src/lyra/blobstore/_handlers.py` | 76 | low | `request.app.state.store` returns `Any`; `# type: ignore[no-any-return]` | Introduce a typed `AppState` dataclass assigned to `app.state` (or an accessor with `assert isinstance`) to give `store` a concrete type |
| `src/lyra/tools/gh_token/helper.py` | 88 | info | `assert isinstance(key, RSAPrivateKey)` after `load_pem_private_key` | Legitimate runtime narrowing; keep as-is |

### Metrics

| Metric | Count |
|--------|-------|
| Files analyzed | 24 |
| `typing.Any` usages | 15 |
| `# type: ignore` comments | 3 |
| Missing return type hints (public functions) | 0 |
| Untyped `**kwargs: Any` | 6 |
| `cast()` usages | 0 |
| `assert isinstance()` usages | 1 |

### Recommendations

1. **Type `_METHOD_TO_OP` as a Literal dict** (`blobstore/auth.py`) — removes the only `type: ignore[arg-type]` in the partition; zero runtime risk.
2. **Add return type to `_make_lifespan`** (`blobstore/serve.py`) — replace the `type: ignore[return]` with an explicit `Callable[[FastAPI], AsyncContextManager[None]]` annotation.
3. **TypedDicts for gh_token JSON boundaries** (`tools/gh_token/helper.py`) — GitHub token response and JWT claims have stable schemas; replacing `dict[str, Any]` improves downstream safety in the dispenser and daemon.
4. **Define `TraceKwargs` / `EventKwargs` TypedDicts** (`obs/base.py`) — once the observability backend contract is finalized (#1235), replace the 6 untyped `**kwargs: Any` spread across Protocol and NoOp.
5. **Typed `AppState` accessor for blobstore** (`blobstore/_handlers.py`) — eliminate the `no-any-return` ignore by assigning a dataclass to `app.state` or adding an `assert isinstance` helper; this also benefits any future handlers.
