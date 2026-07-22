# semctx — change verification

Before finishing a non-trivial change, and before committing:

1. Run `semctx verify diff` (or the MCP tool `semctx_verify_change`).
2. PASS → proceed. WARN → consider a test (not a failure). BLOCK → resolve before finishing.
3. Run the recommended tests.
4. Never declare the work done while a BLOCK is unresolved. Never cite evidence not in the report.

semctx maps a diff to affected symbols, contracts, invariants and tests. It is **not** a
code-search tool and does not "understand the whole repository".

Optional guarded mode (opt-in): create `.semctx/guard.json` with `{ "enabled": true }` to block
`git commit`/`git push` until `semctx verify diff --record` has verified the current diff.
Disable strictly with `SEMCTX_GUARD=off`.
