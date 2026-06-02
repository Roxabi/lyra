# CLAUDE.md — roxabi-nats

## Identity

`roxabi-nats` is a **standalone Python package** — not a Lyra module. It is the
shared NATS transport SDK for the Roxabi plugin ecosystem (voiceCLI, imageCLI,
roxabi-vault, and future services). It lives in the Lyra monorepo for colocation
with the wire contract (ADR-044 (absorbed into ADR-049)/049) but is versioned independently.

→ Architecture contract: `docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx`

## Distribution

¬PyPI. Distributed via GitHub source reference:

```toml
[tool.uv.sources]
roxabi-nats = {
  git = "https://github.com/Roxabi/lyra.git",
  subdirectory = "packages/roxabi-nats",
  tag = "roxabi-nats/vX.Y.Z"
}
```

Lyra itself consumes it via `{ workspace = true }`. Tag pinning is **required**
for external consumers (branch pinning only in plugin-dev branches).

## Split with roxabi-contracts

| Package | Owns |
|---|---|
| `roxabi-nats` | Transport primitives: connection, adapter lifecycle, serialization, circuit breaker, readiness, worker base; wire-side error sanitization helpers (`sanitize_for_wire`) for socket-bound daemon paths |
| `roxabi-contracts` | Wire schemas, `CONTRACT_VERSION`, envelope definitions; sanitization primitives (`scrub_credentials`, `truncate_with_marker`) reused by `WorkerError` and `sanitize_for_wire` |

`CONTRACT_VERSION` canonical home is `roxabi_contracts.envelope`. The compat
re-export in `roxabi_nats.adapter_base` still exists with a DeprecationWarning; scheduled for removal in a future release.

## Public API (stable contract)

Defined by `__all__` in `src/roxabi_nats/__init__.py`. Run `grep __all__ src/roxabi_nats/__init__.py` for the full listing.

`_`-prefixed submodules (`_serialize`, `_sanitize`, `_validate`, `_version_check`,
`_tts_constants`, `_resolver`) are **internal**. External consumers MUST NOT import
them. Lyra (as workspace host) may import them directly — that asymmetry is
intentional and documented in ADR-045.

Testing doubles (`roxabi_nats.testing.*`) are available under the `[testing]`
extra and are stable for test code only.

## Versioning

Package semver (`roxabi-nats/vX.Y.Z` tag) is **independent** from `CONTRACT_VERSION`
(wire-protocol identifier). A `contract_version` bump requires a **major** package
bump and a new contract ADR. `_`-prefixed submodule changes never force a major bump.

Python floor: `>=3.12` (no upper bound — broader than Lyra's `>=3.12,<3.13` to
avoid being a bottleneck when satellites upgrade interpreters).

## Tests

Tests live at `packages/roxabi-nats/tests/` — independently runnable without
the hub venv. Run from the workspace root:

```
uv run pytest packages/roxabi-nats/tests/
```

CI runs this as a distinct job so a hub-only breakage cannot block a plugin
release tag.

## Boundaries

¬import from `lyra.*` (no hub domain dependency). ¬define `lyra.*` NATS subjects
(those belong to contract ADRs). ¬add hub-coupled modules (Cohort B stays in
`src/factory/nats/`). New additions must pass the Cohort A test: zero `lyra.core`
imports.
