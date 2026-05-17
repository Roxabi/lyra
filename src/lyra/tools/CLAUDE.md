# src/lyra/tools/ — In-container helper utilities

Helper processes that run inside `lyra-clipool` but with **isolated identity** from Claude (uid 1500).
Each submodule is self-contained — pure stdlib + project deps, no hub/core imports at module level.

## Submodule map

| Submodule | Process uid | Purpose |
|-----------|-------------|---------|
| `gh_token/` | 1501 (`lyra-gh`) | JWT signer → GitHub installation token → tmpfs cache → dispenser socket → rate-capped refresh |

Shell-script clients in `gh_token/`:
- `git-credential-lyra-gh` — connects to dispenser socket, emits `password=<token>`; `store`/`erase` are no-ops
- `lyra-gh` — fetches token from dispenser, execs `env GH_TOKEN=… gh "$@"`; symlinked as `/usr/local/bin/gh`

## IPC trust boundary

```
uid 1500 (lyra / Claude subprocess)
  │  connects via dispenser.sock
  ▼
/run/lyra-gh-token/          dir  0700  uid 1501 (lyra-gh)
  ├── dispenser.sock         sock 0660  group lyra-tokenuser (gid 1502)
  └── token.cache            file 0600  uid 1501 (lyra-gh)
```

Both uid 1500 and uid 1501 are members of `lyra-tokenuser` (gid 1502) — group membership is the trust boundary; no root needed.

## Key invariants

- Token NEVER enters Claude's subprocess env (uid 1500 ≠ uid 1501, cache 0600 helper-owned)
- Tmpfs dir (`/run/lyra-gh-token/`) is 0700; dispenser socket is 0660 (group `lyra-tokenuser`); cache file is 0600 — each permission serves a distinct purpose
- Helpers do NOT chmod the tmpfs mount — Quadlet sets it at mount time
- All public APIs strictly typed; no framework imports at module level
