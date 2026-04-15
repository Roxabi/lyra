# roxabi-nats

`roxabi-nats` is the shared NATS transport SDK used by the Lyra hub and Roxabi plugins. It provides the low-level NATS client primitives, connection helpers, and typed message contracts that both the hub and plugin ecosystem depend on. Extracted from Lyra as a uv workspace subpackage per [ADR-045](../../docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx); the NATS messaging contract itself is defined in [ADR-044](../../docs/architecture/adr/044-lyra-voicecli-nats-contract.mdx).

## Install (external projects)

```toml
[tool.uv.sources]
roxabi-nats = {
  git = "https://github.com/Roxabi/lyra.git",
  subdirectory = "packages/roxabi-nats",
  tag = "roxabi-nats/v0.1.0"
}
```

## Public API contract

The stable external contract is defined by `__all__` in `roxabi_nats/__init__.py`:

- `NatsAdapterBase` — base class for NATS-backed adapter lifecycles
- `nats_connect` — hardened connection helper (TLS, nkey, creds)
- `CONTRACT_VERSION` — wire-protocol contract version (see ADR-044)

**Underscore-prefixed submodules (`_serialize`, `_sanitize`, `_validate`, `_version_check`, `_tts_constants`) are hub-internal and may change without notice.** External consumers (voiceCLI, roxabi-vault, imageCLI) MUST NOT import from them. Lyra itself, as the workspace host, is the only permitted caller of these internals and does so via explicit `roxabi_nats._submodule` imports — the asymmetry is deliberate and documented in ADR-045.

Tag bumps follow SemVer against the public contract. Changes to `_`-prefixed submodules never force a major bump.
