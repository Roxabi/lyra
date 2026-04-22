# Architecture Analysis: core/processors, core/memory, core/auth

### Summary

The three core subpackages (processors, memory, auth) demonstrate strong architectural hygiene with clear layer boundaries and proper dependency direction. Core modules correctly avoid importing from adapters, llm, or agents layers. However, two issues warrant attention: a hard runtime dependency on `roxabi_vault` in memory.py creates tight coupling to an external package, and runtime imports from `integrations/base.py` in processors (while acceptable for exception types) could benefit from a more formalized protocol pattern.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory.py` | 16 | Hard runtime dependency on external `roxabi_vault.AsyncMemoryDB` - creates tight coupling, violates dependency inversion | Medium | Define a `MemoryDBProtocol` in `lyra.integrations.base` and inject implementation; current design limits testability and future flexibility |
| `/home/mickael/projects/lyra/src/lyra/core/processors/_scraping.py` | 19 | Runtime import from `lyra.integrations.base` (ScrapeFailed) | Low | Acceptable - importing exception types from integrations is appropriate; exceptions are value objects, not service implementations |
| `/home/mickael/projects/lyra/src/lyra/core/processors/vault_add.py` | 23 | Runtime import from `lyra.integrations.base` (VaultWriteFailed) | Low | Acceptable - same rationale as above |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory.py` | 19 | TYPE_CHECKING import from `lyra.infrastructure.stores` | Info | Proper pattern - TYPE_CHECKING imports avoid runtime coupling while enabling type hints |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 17-18 | TYPE_CHECKING imports from `lyra.infrastructure.stores` | Info | Proper pattern - correctly defers infrastructure coupling to type-check time only |

### Metrics

- **Module coupling**: 3/10 (low coupling, good isolation)
  - processors: 2 internal deps, 1-2 integrations deps (exceptions)
  - memory: 4 internal deps, 1 external package dep, 1 infrastructure TYPE_CHECKING dep
  - auth: 2 internal deps, 2 infrastructure TYPE_CHECKING deps

- **Circular deps**: 0 (none detected within or between these subpackages)

- **Layer violations**: 1 medium (roxabi_vault runtime dep), 2 low (exception imports from integrations - acceptable)

### Architectural Strengths

1. **Clean layer boundaries**: No imports from adapters, llm, or agents layers in any of the three subpackages.

2. **Proper TYPE_CHECKING usage**: Infrastructure imports are correctly isolated to TYPE_CHECKING blocks in `authenticator.py` and `memory.py`.

3. **GuardChain pattern**: `auth/guard.py` demonstrates excellent Protocol-based composition with `Guard` protocol and `GuardChain` orchestrator.

4. **Mixin decomposition**: `memory_upserts.py` cleanly separates write operations as a mixin base class, keeping `memory.py` focused on initialization and read operations.

5. **Processor registry**: Self-registration pattern via `@register` decorator enables open/closed principle - new processors add without modifying core files.

### Recommendations

1. **[Medium Priority] Decouple memory from roxabi_vault**: Create a `MemoryDBProtocol` in `lyra.integrations.base` defining the async interface (`connect`, `close`, `search`, `save_entry`, `upsert_session`). Have `MemoryManager` accept an injected implementation. This enables:
   - Swappable backends (e.g., PostgreSQL, cloud memory services)
   - Test doubles for unit testing without SQLite
   - Future-proofing for architecture changes

2. **[Low Priority] Document integrations exception pattern**: The current pattern of importing exception types from `integrations/base.py` is sound but should be documented in ADR to prevent accidental import of implementation classes.

3. **[Info] Consider extracting VaultProvider exception handling**: The vault processors (search.py, vault_add.py) handle `VaultWriteFailed` gracefully. Document this pattern as a reference for future session commands.
