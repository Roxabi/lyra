# Security Audit: core/agent + core/cli

### Summary
The codebase demonstrates strong security practices overall with proper input validation, parameterized SQL queries, and safe deserialization. However, several areas warrant attention including the `skip_permissions` feature that bypasses security controls, temp file handling with potential race conditions, and the `/folder` command that could enable path traversal if called by untrusted users.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_protocol.py` | 78-79 | `--dangerously-skip-permissions` flag passed to CLI when `skip_permissions=True`, bypassing tool permission prompts | **High** | Document security implications clearly; consider audit logging when this flag is used; restrict to trusted agents only |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_protocol.py` | 84-89 | Temp file for system prompt uses `mkstemp` but chmod 0o600 happens after write - TOCTOU window | **Medium** | Set umask before mkstemp or use `os.open` with O_EXCL; file is already created with restricted permissions by mkstemp, but explicit chmod timing is suboptimal |
| `/home/mickael/projects/lyra/src/lyra/core/commands/workspace_commands.py` | 26-27 | `/folder` command accepts arbitrary path from user input; relies on admin-only restriction via `require_admin()` | **Medium** | Add explicit path sanitization (reject `..`, check against allowlist if possible); verify admin check cannot be bypassed |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner.py` | 289-291 | API key read from `ANTHROPIC_API_KEY` env var and passed to SDK client; key is in memory but not logged | **Low** | Consider using secret management system; ensure key is not exposed in stack traces |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_worker.py` | 121-122 | Environment allowlist `_SAFE_ENV_KEYS` restricts subprocess env, but `HOME` is added separately; `PATH` could enable binary hijacking | **Low** | Consider using absolute path for `claude` binary; validate PATH entries don't point to world-writable directories |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_config.py` | 75 | `api_key` field properly excluded from serialization (`exclude=True, repr=False`) | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_schema.py` | 94-124 | SQL queries use parameterized placeholders (`?`) - no SQL injection | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_builder.py` | 38-42 | Model name validated with regex `^[a-zA-Z0-9_.:-]+$` - prevents command injection | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_builder.py` | 49-57 | `cwd` path validated (must be existing directory) - mitigates path traversal | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_protocol.py` | 97-99 | Session ID validated with strict UUID regex before use in `--resume` | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_session.py` | 42 | Session ID validation applied before persisting | **Info** | Good practice - no action needed |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner.py` | 86-91 | `RefinementPatch` validates fields against allowlist `REFINABLE_FIELDS` - prevents arbitrary field modification | **Info** | Good practice - no action needed |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | 1 (skip_permissions bypass) |
| A02:2021 - Cryptographic Failures | 0 |
| A03:2021 - Injection | 0 (SQL uses parameterized queries; model name validated) |
| A04:2021 - Insecure Design | 1 (temp file TOCTOU) |
| A05:2021 - Security Misconfiguration | 1 (PATH in subprocess env) |
| A06:2021 - Vulnerable Components | N/A (dependency scanning required) |
| A07:2021 - Identification and Authentication Failures | 0 (admin checks in place) |
| A08:2021 - Software and Data Integrity Failures | 0 (safe deserialization with json/tomllib) |
| A09:2021 - Security Logging and Monitoring Failures | 1 (no audit logging for skip_permissions usage) |
| A10:2021 - Server-Side Request Forgery | 0 |

### Recommendations (Prioritized)

1. **High Priority**: Implement audit logging when `skip_permissions=True` is configured or `--dangerously-skip-permissions` is passed to CLI subprocess. This creates an accountability trail for security-critical operations.

2. **Medium Priority**: Add defense-in-depth path validation for `/folder` command beyond the admin-only check. Consider:
   - Rejecting paths containing `..`
   - Validating against a configurable allowlist of root directories
   - Logging all workspace switches

3. **Medium Priority**: Review temp file creation pattern in `cli_protocol.py` and `middleware_stt.py`. While `mkstemp` creates files with 0600 permissions atomically, the subsequent `os.chmod()` is redundant and creates a timing window. Remove the chmod call since mkstemp already provides restricted permissions.

4. **Low Priority**: Consider using an absolute path for the `claude` binary instead of relying on PATH lookup, or validate that PATH entries don't point to attacker-controlled directories.

5. **Low Priority**: Document security assumptions about the `plugins_dir` being a trusted operator-controlled directory (as noted in `agent_commands.py` line 55-56 comment).

### Positive Security Practices Observed

- No hardcoded credentials found
- API keys properly excluded from serialization and repr
- All SQL queries use parameterized placeholders
- No use of `pickle`, `yaml.load`, or `eval/exec`
- No `shell=True` in subprocess calls
- Input validation via regex for model names, session IDs, and workspace keys
- Environment variable allowlist for subprocess spawning
- SHA-256 hash verification for hot-reload (prevents TOCTOU on plugin files)
- Admin-only restriction on sensitive commands (`/folder`, `/config`, `/circuit`, `/routing`)
- Safe JSON parsing throughout (no `eval` on parsed content)
