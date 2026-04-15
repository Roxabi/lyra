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
