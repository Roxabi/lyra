# src/lyra/tools/ — In-container helper utilities

Helper processes that run inside `lyra-clipool` but with **isolated identity** from Claude (uid 1500).
Each helper module is self-contained — pure stdlib + project deps, no hub/core imports at module level.

## gh_token/ — dispenser

`gh_token/` runs as uid 1501 (`lyra-gh`): JWT signer → GitHub installation token → tmpfs cache → dispenser socket → rate-capped refresh. Run `ls src/lyra/tools/gh_token/` for the current module inventory.

## IPC trust boundary

```
uid 1500 (lyra / Claude subprocess)
  │  connects via dispenser.sock
  ▼
/run/lyra-gh-token/          dir  0700  uid 1501 (lyra-gh)
  ├── dispenser.sock         sock 0660  group lyra-tokenuser (gid 1502)
  └── token.json             file 0600  uid 1501 (lyra-gh)
```

Both uid 1500 and uid 1501 are members of `lyra-tokenuser` (gid 1502) — group membership is the trust boundary; no root needed.

## Key invariants

- Token NEVER enters Claude's subprocess env (uid 1500 ≠ uid 1501, cache 0600 helper-owned)
- Tmpfs dir (`/run/lyra-gh-token/`) is 0700; dispenser socket is 0660 (group `lyra-tokenuser`); cache file is 0600 — each permission serves a distinct purpose
- Helpers do NOT chmod the tmpfs mount — Quadlet sets it at mount time
- All public APIs strictly typed; no framework imports at module level
