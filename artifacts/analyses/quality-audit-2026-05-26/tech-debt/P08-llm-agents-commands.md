# P08 Tech-Debt Audit — LLM, Agents, Commands, AgentCmd

**Date:** 2026-05-27
**Scope:** `src/lyra/llm/**/*.py`, `src/lyra/agents/**/*.py`, `src/lyra/commands/**/*.py`, `src/lyra/agent_cmd/**/*.py`
**Files:** 29
**Prior audit:** 2026-05-18 (hexagonal conformance, mutualisation, simplification) — regressions not re-reported unless new.

---

## Summary

- **Clean surface, leaky boundaries.** Zero TODO/FIXME markers across 29 files, but three high-severity structural leaks remain: `LlmClient` punches through `WorkerPoolClient` to call private `_transport`, `commands/identity` uses reflection (`getattr`) on private hub attributes, and `simple_agent.py` still registers session commands despite ADR-031 deprecation.
- **Legacy shim drift.** Two backward-compatibility re-export modules (`llm/base.py`, `llm/llm_codec.py`) and a Pydantic v1/v2 runtime check in `cli_pool_codec.py` continue to compile but add indirection. No consumer-migration plan is tracked.
- **Exemption hygiene gap.** `tools/file_exemptions.txt` still lists `src/lyra/llm/drivers/cli_nats.py` (deleted in #1281). `simple_agent.py` sits at 313 lines with an exemption anchored to issues #1008 and #1225; neither issue is actively driving a shrink plan.

---

## Findings

| # | File (line ref) | Finding | Sev | Category | Notes |
|---|-----------------|---------|-----|----------|-------|
| 1 | `src/lyra/llm/llm_client.py` (156, 186, 209) | `self._pool._transport.call()` — private access bypass | High | API boundary leak | `WorkerPoolClient` exposes `request_with_routing()` and `stream_request()` but no public `call()`. Control-plane methods (`reset`, `resume_and_reset`, `switch_cwd`) are forced to `# noqa: SLF001`. |
| 2 | `src/lyra/commands/identity/handlers.py` (33, 37, 44, 47, 91) | `getattr` on `_ctx`, `_alias_store`, `_authenticators`; `auth._store.check()` | High | Boundary violation | Reflection reaches into hub/transport private state. No public port exists for alias/identity lookup from the command layer. |
| 3 | `src/lyra/agents/simple_agent.py` (148-156) | `/add-vault` registered via `register_session_command()` | Medium | Deprecated pattern | ADR-031 deprecates session commands. Modern `BaseProcessor` equivalents exist (`/search` → `SearchProcessor`, `/vault-add` → `VaultAddProcessor`). |
| 4 | `src/lyra/commands/search/handlers.py` (30) | Hardcoded `timeout=25.0` | Low | Magic number | No config source; differs from `NatsTransport` default_timeout (300 s). |
| 5 | `src/lyra/commands/add_vault/handlers.py` (26-27, 59) | `_MAX_CONTENT_CHARS = 32_000`, `_TITLE_MAX_CHARS = 80`, `timeout=30.0` | Low | Magic numbers | Content-limit constants should be config-driven or live in the processor, not the legacy handler. |
| 6 | `src/lyra/agents/simple_agent.py` (42) | Direct import `from lyra.infrastructure.stores.agent_store import AgentStore` | Medium | ADR-048 violation | Core (`agents/`) imports infrastructure implementation. Should consume a protocol or be injected. |
| 7 | `src/lyra/llm/cli_pool_codec.py` (54) | Pydantic v1/v2 runtime shim: `hasattr(model_cfg, "model_dump")` | Low | Legacy shim | Runtime branching for `model_dump` vs dict. Safe to drop once v1 consumers are fully retired. |
| 8 | `src/lyra/llm/base.py` | Backward-compat re-export of `LlmProvider`, `LlmResult` from `lyra.core.ports.llm` | Low | Legacy shim | Consumers should import from canonical `lyra.core.ports.llm`. No deprecation warning emitted. |
| 9 | `src/lyra/llm/llm_codec.py` | Backward-compat re-export: `CliNatsCodec as LlmCodec` | Low | Legacy shim | Same as #8 — indirection without deprecation signal. |
| 10 | `src/lyra/agents/simple_agent.py` (205) | `# noqa: C901` on `process()` | Low | Complexity residual | Cyclomatic-complexity override; no active issue to refactor. |
| 11 | `src/lyra/agent_cmd/agents/init.py` (70) | `# noqa: C901` on `validate()` | Low | Complexity residual | Same pattern as #10. |
| 12 | `src/lyra/llm/decorators.py` | `# noqa: PLR0913` on `complete()` / `stream()` | Low | Parameter bloat | Bootstrap wiring debt; multiple kwargs forwarded to underlying provider. |
| 13 | `tools/file_exemptions.txt` (21) | Stale exemption for deleted `src/lyra/llm/drivers/cli_nats.py` | Low | Exemption hygiene | File removed in #1281; line 21 of `file_exemptions.txt` is dead. |
| 14 | `src/lyra/agents/simple_agent.py` | File-length exemption aging (313 lines, cap 300) | Low | Exemption debt | Exemption cites #1008 / #1225. No open issue driving a shrink below 300 lines. |

---

## Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Files in scope | 29 | — |
| TODO / FIXME count | 0 | 0 |
| `register_session_command()` usages | 1 active (`/add-vault` in `simple_agent.py`) | 0 |
| Private-attribute reflection sites | 5 (`commands/identity/handlers.py`) | 0 |
| Private `_transport.call()` bypasses | 3 (`llm_client.py`) | 0 |
| ADR-048 direct infra imports | 1 (`AgentStore` in `simple_agent.py`) | 0 |
| File-size exemptions (P08 files) | 1 (`simple_agent.py`) | 0 |
| Stale exemptions | 1 (`cli_nats.py`) | 0 |
| `# noqa: C901` annotations | 2 | 0 |
| `# noqa: PLR0913` annotations | 2+ | 0 |
| Magic-number constants | 4 (`25.0`, `30.0`, `32_000`, `80`) | config-driven |

---

## Recommendations

1. **Add public `call()` to `WorkerPoolClient`** and migrate `LlmClient` control-plane methods. Removes the only `# noqa: SLF001` site in P08 and closes the structural API gap. (Impact: High — blocks clean transport boundary.)
2. **Extract identity/alias lookup into a public port** (e.g., `IdentityResolver` Protocol) and inject it into `commands/identity`. Eliminates `getattr` on `_ctx`, `_alias_store`, `_authenticators`. (Impact: High — security-adjacent boundary.)
3. **Delete `/add-vault` session-command registration** from `simple_agent.py` and retire `commands/add_vault/handlers.py` once processor command coverage is confirmed complete. Aligns with ADR-031 and removes duplicated feature surface. (Impact: Medium — feature duplication.)
4. **Prune stale exemption** for `src/lyra/llm/drivers/cli_nats.py` from `tools/file_exemptions.txt`. One-line cleanup with zero risk. (Impact: Low — hygiene.)
5. **Open shrink-issue for `simple_agent.py`** or convert the `AgentStore` import to protocol-based injection (ADR-048). Either action would drop the file below 300 lines and retire its exemption. (Impact: Medium — compliance + hygiene.)
