# roxabi-nats

`roxabi-nats` is the shared NATS transport SDK used by the Lyra hub and Roxabi plugins. It provides the low-level NATS client primitives, connection helpers, and typed message contracts that both the hub and plugin ecosystem depend on. Extracted from Lyra as a uv workspace subpackage per [ADR-045](../../docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx); the NATS messaging contract itself is defined in [ADR-044 (absorbed into ADR-049)](../../docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx).

## Install (external projects)

```toml
[tool.uv.sources]
roxabi-nats = {
  git = "https://github.com/Roxabi/roxabi-factory.git",
  subdirectory = "packages/roxabi-nats",
  tag = "roxabi-nats/vX.Y.Z"  # pick a real tag — see below
}
```

Resolve the latest released tag (never hardcode one that may not exist):

```bash
git ls-remote --tags https://github.com/Roxabi/roxabi-factory.git 'roxabi-nats/*'
# or, in a local clone:
git tag -l 'roxabi-nats/*' | sort -V | tail -1
```

Pin by **tag**, not branch — see [Pin doctrine](../../docs/architecture/contracts.md#pin-doctrine-external-consumers).

## Public API contract

The stable external contract is exactly the names in `__all__` in `roxabi_nats/__init__.py` (source is SSoT — `grep __all__ src/roxabi_nats/__init__.py`). It currently exports:

| Name | Role |
|---|---|
| `NatsAdapterBase` | Base class for NATS-backed adapter lifecycles |
| `NatsDriverBase` | Reusable hub-side request-dispatch base for worker drivers |
| `WorkerUnavailableError` | Raised when a worker's heartbeat stops during an active stream |
| `nats_connect` | Hardened connection helper (TLS, nkey, creds) |
| `TypeHintResolver` | Public wrapper resolving `TYPE_CHECKING`-only hints during (de)serialization |
| `sanitize_for_wire` | Sanitize an exception into a wire-safe, length-capped string |
| `DEFAULT_MAX_LEN` | Default cap (chars) applied by `sanitize_for_wire` |
| `CONTRACT_VERSION` | Wire-protocol contract version (compat re-export; canonical home is `roxabi_contracts.envelope`, see ADR-044 (absorbed into ADR-049)) |

Public utility submodule `errors` also exports `sanitize_for_wire` / `DEFAULT_MAX_LEN`.

**Underscore-prefixed submodules (`_serialize`, `_sanitize`, `_validate`, `_version_check`, `_tts_constants`) are hub-internal and may change without notice.** External consumers (voiceCLI, roxabi-vault, imageCLI) MUST NOT import from them. Lyra itself, as the workspace host, is the only permitted caller of these internals and does so via explicit `roxabi_nats._submodule` imports — the asymmetry is deliberate and documented in ADR-045.

Tag bumps follow SemVer against the public contract. Changes to `_`-prefixed submodules never force a major bump.

## Operator note: inbox_prefix is required for nkey-authenticated identities

Every NATS identity that authenticates with an nkey seed and holds a scoped inbox ACL grant (e.g. `_inbox.hub.>`) **MUST** supply either `identity_name` or `inbox_prefix` when calling `nats_connect`. Omitting both causes nats-py to use the default uppercase `_INBOX.<random>.>` prefix, which does not match the narrowed ACL and produces:

```
nats: permissions violation for subscription to "_inbox.<nuid>.*"
```

**Correct usage** (per ADR-051):

```python
# Preferred: identity_name derives inbox_prefix="_inbox.{name}" automatically
nc = await nats_connect(nats_url, identity_name="hub")

# Equivalent explicit form (legacy callers)
nc = await nats_connect(nats_url, inbox_prefix="_inbox.hub")
```

The two parameters are mutually exclusive. Bare `nats_connect(url)` calls are only safe in dev/anonymous mode (no nkey seed set, no inbox ACL).
