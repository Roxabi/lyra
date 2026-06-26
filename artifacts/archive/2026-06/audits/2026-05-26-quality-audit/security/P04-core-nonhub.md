# Security Audit — Partition P04: core/ (non-hub)

**Date:** 2026-05-26
**Scope:** `src/lyra/core/**/*.py` excluding `src/lyra/core/hub/`
**Files scanned:** 88 Python files
**Focus:** OWASP Top 10, credential handling, injection, path traversal, SSRF, GH token scope, NATS ACLs

---

### Summary

- **Sensitive data exposure:** `TelegramTokenFilter` regex (`trace.py`) is too narrow — it omits `.`, `+`, `/` characters that can appear in bot tokens, allowing partial tokens to leak into logs.
- **Path traversal:** Admin-only commands `/folder` and `/workspace` resolve arbitrary filesystem paths without a base-directory constraint; `LYRA_CLAUDE_CWD` env var is also unconstrained at module load.
- **GH token scope gap:** `mint()` requests a full-scoped installation token from GitHub instead of enforcing least-privilege `permissions` at runtime.
- **Positive defenses observed:** all SQLite queries are parameterized; Telegram/Discord secrets are env-only with `repr=False`; plugin loader validates names and symlink escapes; SSRF pre-check blocks private IPs; token-redaction filter is attached to root logger and all handlers.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/core/trace.py` | 123, 131 | **Medium** | `TelegramTokenFilter` regex `[A-Za-z0-9_-]+` excludes `.`, `+`, `/` (and `=`) which may appear in Telegram bot tokens; any token containing those characters is **not redacted** and leaks to logs / log-shipping. | Expand secret character class to `[A-Za-z0-9+/._=-]+` or switch to a non-greedy `\S+` boundary so the filter stays future-proof against token alphabet changes. |
| `src/lyra/tools/gh_token/helper.py` | 187-221 | **Medium** | `mint()` POSTs to `/app/installations/{id}/access_tokens` with **no request body**; the returned token inherits the **full permissions** of the GitHub App installation (e.g., issues, PRs, admin), violating least privilege for a git-credential helper. | Add a JSON body: `{"permissions":{"contents":"read"}}` (or the minimal set required). Optionally restrict `repositories` if the installation covers more repos than needed. |
| `src/lyra/core/commands/workspace_commands.py` | 26 | **Low** | `/folder` resolves `Path(args[0]).expanduser().resolve()` with **no base-directory guard**; while admin-only, a compromised admin account can redirect the CLI subprocess cwd to any directory (`/etc`, `/root`, etc.). | Enforce `raw_path.is_relative_to(TRUSTED_ROOT)` before calling `pool.switch_workspace(raw_path)`. Apply the same guard to `/workspace` switching. |
| `src/lyra/core/cli/cli_pool_worker.py` | 68-69 | **Low** | `LYRA_CLAUDE_CWD` is read from `os.environ` at **module load time** with zero validation. In a misconfigured or compromised container, an attacker-controlled env var can hijack the default subprocess cwd. | Resolve the path and assert `is_relative_to(TRUSTED_ROOT)` or require an explicit allowlist file; fail fast with a clear error if the constraint is violated. |
| `src/lyra/core/processors/_scraping.py` | 30-91 | **Low** | SSRF defense resolves the hostname **before** scraping via `socket.getaddrinfo`, but there is **no connection-level or redirect guard**; DNS TTL attacks / rebinding could cause the actual HTTP connection to hit a private IP after the check passes. | Pin the resolved IP in the scraper HTTP client (e.g., `httpx` with custom resolver), or validate the final redirect URL against `_is_private_ip` before following it. |
| `src/lyra/core/agent/agent_refiner.py` | 137 | **Low** | `subprocess.run(["claude", "--print", prompt])` passes user/LLM-controlled `prompt` as a single CLI argument. If the `claude` parser mishandles leading `-` characters, argument injection is possible. | Sanitize `prompt` by stripping leading `-` or prefix it with `--` before the prompt string to force positional-arg parsing in the downstream CLI. |
| `src/lyra/core/stores/json_agent_store.py` | 46-47, 215 | **Info** | Documented as **test-only**, but `_persist()` writes agent JSON to the constructor-supplied `path` with **no path validation**; accidental production instantiation could exfiltrate or overwrite agent data. | Add an explicit `assert "test" in str(self._path).lower()` guard in `__init__` or rename the class to `TestJsonAgentStore` to make the scope unmistakable. |

---

### Metrics

| Metric | Value |
|---|---|
| Files scanned | 88 |
| Total findings | 7 |
| Medium | 2 |
| Low | 4 |
| Info | 1 |
| **OWASP coverage** | |
| A01 — Broken Access Control | 2 findings (admin path traversal, env cwd injection) |
| A02 — Cryptographic Failures | 0 (no custom crypto in scope) |
| A03 — Injection | 1 finding (CLI arg injection in refiner) |
| A04 — Insecure Design | 0 |
| A05 — Security Misconfiguration | 2 findings (token regex, env cwd) |
| A06 — Vulnerable Components | 0 |
| A07 — Auth Failures | 0 (auth handled in hub/ and infrastructure/) |
| A08 — Software / Data Integrity | 0 |
| A09 — Security Logging / Monitoring | 0 (logging redaction is present) |
| A10 — SSRF | 1 finding (scraping DNS-rebinding gap) |
| **Positive defenses** | Parameterized SQL (`memory_upserts.py`), env-only credentials (`config.py`), `repr=False` on secrets, `TelegramTokenFilter` + `TraceIdFilter`, plugin path/symlink guards (`command_loader.py`), SSRF IP block (`_scraping.py`), GH install_id numeric validation (`helper.py`), subprocess env allowlist (`cli_pool_worker.py`), `html.escape` in search/scraping processors |

---

### Recommendations (prioritized)

1. **Fix token-redaction regex immediately.** The `[A-Za-z0-9_-]+` character class is too restrictive for Telegram bot tokens. Expand it to cover base64 and base64url alphabets (`+`, `/`, `.`, `=`) so the filter never silently misses a token.
2. **Enforce least-privilege GH token minting.** Pass a `permissions` JSON body in `mint()` to restrict the installation token to only the scopes required for git operations (e.g., `contents: read`). This limits blast radius if the token cache or Unix socket is ever compromised.
3. **Add base-directory guards to admin path commands.** `/folder` and `/workspace` should validate the resolved path against a configured `TRUSTED_ROOT` (e.g., `~/.lyra/workspaces/` or `_LYRA_ROOT`). Do the same for `LYRA_CLAUDE_CWD` at module load.
4. **Harden SSRF defense at the connection layer.** Complement the existing DNS pre-check with a scraper-level guard: either pin the resolved IP in the HTTP transport or re-validate every redirect target with `_is_private_ip` before following it.
5. **Sanitize the `AgentRefiner` subprocess prompt.** Strip or escape leading `-` characters, or insert a `--` separator before the prompt in the `subprocess.run` args list to prevent the `claude` CLI from interpreting user/LLM text as flags.
