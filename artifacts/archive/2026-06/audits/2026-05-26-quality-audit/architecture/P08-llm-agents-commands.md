### Summary

- Importlinter reports **8/8 contracts kept** (0 broken), confirming no import-time layer violations or circular dependencies in partition P08.
- New `llm_client.py` control-plane methods reach into `WorkerPoolClient._transport` directly, violating the 3-layer abstraction and blocking the future HTTP transport swap.
- Two new LLM codecs and one unaudited command handler import or reflect into non-port core modules (`core.messaging.events`, `core.trace`, `Pool._ctx` / `Hub._authenticators`), creating runtime cross-stage coupling not caught by importlinter.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/llm/llm_client.py` | 156, 186, 209 | **High** | `reset()`, `resume_and_reset()`, and `switch_cwd()` call `self._pool._transport.call(...)` directly, bypassing the `WorkerPoolClient` public API and coupling the domain client to NATS `.call()` semantics. This breaks the 3-layer composition (LlmClient → WorkerPoolClient → NatsTransport) and prevents swapping the underlying transport for HTTP (P4). | Add `call(subject, payload, timeout)` to `WorkerPoolClient` public API (or introduce a `ControlPlane` port) so `LlmClient` delegates without touching private `_transport`. |
| `src/lyra/llm/cli_nats_codec.py` | 20 | **Medium** | `CliNatsCodec` imports `LlmEvent`, `ResultLlmEvent`, and `TextLlmEvent` directly from `lyra.core.messaging.events`. The LLM layer should not depend on the core event taxonomy; this is a cross-stage import not mediated by a port or contract schema. | Define neutral `LlmDomainEvent` types in `core/ports/llm` and map them to `LlmEvent` in the outbound stage (`StreamProcessor` or emitter). |
| `src/lyra/llm/cli_pool_codec.py` | 23 | **Medium** | `CliPoolCodec` imports `TraceContext` from `lyra.core.trace` at runtime to read `agent_name`. This couples the codec to the core tracing implementation instead of receiving the value explicitly. | Pass `agent_name` explicitly via `encode()` kwargs and remove the `TraceContext` import. |
| `src/lyra/commands/identity/handlers.py` | 33–47 | **Medium** | `_any_alias_blocked()` and `_get_alias_store()` use `getattr` to reach into `Pool._ctx`, `Hub._authenticators`, and `auth._store`. This is private-state reflection that bypasses ports and leaks bootstrap/session internals into a command handler. | Inject `IdentityAliasStoreProtocol` and `Authenticator` instances directly into the handler (or expose them as public properties on `Pool`) instead of reflecting through private attributes. |
| `src/lyra/llm/llm_codec.py` | 1–8 | **Low** | New backward-compatibility shim added in #1281 re-exporting `CliNatsCodec` as `LlmCodec`. The prior audit explicitly targeted dead-code shims for removal (cluster 1.1–1.4); adding a new shim regresses dead-code hygiene. | Schedule deletion once all consumers migrate to `lyra.llm.cli_nats_codec`; track in the dead-code cluster issue. |

---

### Metrics

| Metric | Value |
|---|---|
| Files analyzed in P08 | 28 |
| Files changed since 2026-05-18 | 8 (+10 deleted in diff) |
| Importlinter contracts kept | 8 / 8 (**100%**) |
| New runtime layer leaks in new code | 3 |
| Unaudited gaps with private reflection / direct cross-stage imports | 2 |

---

### Recommendations (prioritized)

1. **Seal the `WorkerPoolClient` boundary** — Add a `call()` method (or `ControlPlane` port) to `WorkerPoolClient` so `LlmClient` control-plane methods delegate through the public API instead of reaching into `_transport`. This unblocks the HTTP transport swap planned in P4.
2. **Decouple LLM codecs from `core.messaging.events`** — Introduce neutral LLM domain types in `core/ports/llm` and let the outbound stage map them to `LlmEvent`. This isolates the codec layer from the stage-axis event taxonomy.
3. **Remove `TraceContext` from `CliPoolCodec`** — Pass `agent_name` explicitly via `encode()` kwargs. Low effort, removes a cross-cutting dependency.
4. **Eliminate private-state reflection in `identity` handlers** — Promote `_alias_store` and `_authenticators` to public properties on `Pool`/`Hub`, or inject the required ports directly into the handler. Improves testability and security posture.
5. **Delete `llm_codec.py` shim** — Align with the prior audit's dead-code cluster; schedule removal after consumer migration and close the loop on shim hygiene.
