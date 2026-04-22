# Security Audit: core/commands, core/stores, core/pool, core/messaging

### Summary
The core modules (commands, stores, pool, messaging) demonstrate generally strong security practices with parameterized SQL queries, path traversal protections in the plugin loader, and admin-only guards on sensitive commands. However, several areas warrant attention: the `/folder` command allows unrestricted directory traversal for admins, dynamic module loading in plugins presents code execution risk if manifests are compromised, and one SQL IN clause uses f-string construction (though values remain parameterized).

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/commands/workspace_commands.py` | 26 | Path Traversal - `/folder` command accepts arbitrary path from admin user without workspace confinement | Medium | Consider adding configurable allowed_directories whitelist or documenting this as intentional admin privilege |
| `/home/mickael/projects/lyra/src/lyra/core/commands/command_loader.py` | 157-168 | Dynamic Code Execution - Plugins loaded via importlib with exec_module() - compromised plugin.toml could enable arbitrary code execution | Medium | Add plugin signature verification or hash checking; consider sandboxing plugin execution |
| `/home/mickael/projects/lyra/src/lyra/core/stores/prefs_store.py` | 93-96 | SQL Construction with f-string - IN clause built with f-string `f"SELECT ... IN ({placeholders})"` though values are parameterized | Low | Refactor to use a more explicit parameter binding approach for consistency with other stores |
| `/home/mickael/projects/lyra/src/lyra/core/commands/command_loader.py` | 114 | Broad Exception Catching - `except Exception` silently skips malformed plugin.toml, potentially hiding security-relevant parse errors | Low | Log specific exception types or add security event logging for malformed manifests |
| `/home/mickael/projects/lyra/src/lyra/core/stores/json_agent_store.py` | 71-74 | Insecure Deserialization Risk - JSON loaded and used to construct AgentRow objects; if LYRA_DB=json is set in production, malicious JSON could cause issues | Low | Ensure LYRA_DB=json is never used in production; add explicit environment check/warning |
| `/home/mickael/projects/lyra/src/lyra/core/commands/builtin_commands.py` | 183 | Hardcoded Path - `_AGENTS_DIR` derived from `__file__` location is safe but fragile if package structure changes | Info | Consider making this configurable via settings |
| `/home/mickael/projects/lyra/src/lyra/core/stores/pairing.py` | 153-159 | Timing Attack Comment - Code acknowledges SQL WHERE clause is not constant-time but dismisses risk due to SHA-256 pre-hashing | Info | Acceptable for personal-use scale; no action needed |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | 1 (admin-only path traversal) |
| A03:2021 - Injection | 2 (SQL IN clause, dynamic code loading) |
| A04:2021 - Insecure Design | 1 (broad exception handling) |
| A05:2021 - Security Misconfiguration | 1 (test store in production risk) |
| A07:2021 - Identification and Authentication Failures | 0 |
| A08:2021 - Software and Data Integrity Failures | 1 (unsigned plugin loading) |
| A09:2021 - Security Logging and Monitoring Failures | 1 (silent malformed manifest handling) |
| A02:2021 - Cryptographic Failures | 0 |

### Recommendations

1. **High Priority - Plugin Integrity**: Add cryptographic signature verification for plugin manifests and handler modules. The path traversal protections in `command_loader.py` (lines 101-146) are excellent, but a compromised `plugin.toml` could still lead to arbitrary code execution.

2. **Medium Priority - Admin Path Access**: Document or restrict the `/folder` command. Currently admins can switch to any accessible directory. Either:
   - Add `allowed_workspaces` validation
   - Document this as an intentional admin privilege requiring trusted users

3. **Low Priority - SQL Consistency**: Refactor `prefs_store.py` line 93-96 to match the parameterized query pattern used in other stores for consistency and to avoid any edge-case injection scenarios with unusual alias values.

4. **Low Priority - Test Store Protection**: Add a runtime warning in `json_agent_store.py` when `LYRA_DB=json` is set, reminding developers this is test-only and should never be used in production.

5. **Info - Security Logging**: Add explicit security event logging in `command_loader.py` for malformed manifests or path traversal attempts, rather than silently skipping.
