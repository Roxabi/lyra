# roxabi-contracts

Shared Pydantic schemas for Lyra cross-project NATS contracts. Per-domain submodules (voice, image, memory, llm) import `ContractEnvelope` from this package as their common base. Extracted from Lyra as a uv workspace subpackage per [ADR-049](../../docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx).

## Install (external projects)

```toml
[tool.uv.sources]
roxabi-contracts = {
  git = "https://github.com/Roxabi/lyra.git",
  subdirectory = "packages/roxabi-contracts",
  tag = "roxabi-contracts/v0.1.0"
}
```

## Public API contract

The stable external contract is defined by `__all__` in `roxabi_contracts/__init__.py`. v0.1.0 ships:

- `ContractEnvelope` — base Pydantic model for all per-domain contract schemas

Future domain submodules (voice, image, memory, llm) arrive in subsequent tags. See ADR-049 §Versioning for SemVer rules.
