# src/lyra/tools/ — In-container helper utilities

Helper processes that run inside `lyra-clipool` but with **isolated identity** from Claude (uid 1500).
Each submodule is a self-contained utility — pure stdlib + project deps, no hub/core imports.

TODO(T3): add Unix socket dispenser
TODO(T4): add refresh_loop + rate caps

## Submodule map

| Submodule | Process uid | Purpose |
|-----------|-------------|---------|
| `gh_token/` | 1501 (`lyra-gh`) | JWT signer → GitHub installation token → tmpfs cache → dispenser socket |

## Key invariants

- Token NEVER enters Claude's subprocess env (uid 1500 ≠ uid 1501, cache 0600 helper-owned)
- Tmpfs parent (`/run/lyra-gh-token/`) is 0700, mounted by Quadlet — helpers do NOT chmod it
- All public APIs strictly typed; no framework imports at module level
