# Security Audit: Bootstrap

### Summary
The bootstrap package demonstrates generally strong security practices, including proper use of environment variables for secrets, timing-safe HMAC comparisons, and validated SQL identifier interpolation. However, several issues were identified including a potential path traversal vulnerability in secret reading, inconsistent HMAC comparison patterns, and the use of `sys.exit()` in library code.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/health.py | 41-53 | Path traversal vulnerability in `_read_secret()` - no validation of `name` parameter | Medium | Add path traversal validation (e.g., reject `..`, `/`, `\`) or use a whitelist approach |
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/health.py | 142-144 | Inconsistent HMAC comparison - compares strings directly unlike lines 82-84 which encode to bytes | Low | Encode both sides to bytes for consistency with `health_detail()` endpoint |
| /home/mickael/projects/lyra/src/lyra/bootstrap/bootstrap_stores.py | 105, 113, 117 | SQL string interpolation (MITIGATED) - uses f-strings for table/column names but validates with `_IDENT_RE` regex first | Low (Mitigated) | Consider documenting the validation approach more clearly or using parameterized queries for identifiers |
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/lockfile.py | 39-64 | TOCTOU race condition - time gap between checking if PID is alive and overwriting lockfile | Low | Accept as documented limitation (single-instance deployment) or use atomic file locking |
| /home/mickael/projects/lyra/src/lyra/bootstrap/wiring/bootstrap_wiring.py | 279 | `sys.exit()` in library function - prevents graceful error handling by callers | Low | Raise `ConfigurationError` exception instead and let caller decide exit behavior |
| /home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py | 94-96, 111-116 | Multiple `sys.exit()` calls in bootstrap function | Low | Raise exceptions and handle at CLI entry point only |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/stt_adapter_standalone.py | 109-124 | Temporary file handling - creates temp files with user-controlled MIME type suffix (mitigated by dictionary lookup) | Low (Mitigated) | Already mitigated by `_mime_to_ext()` dictionary lookup |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | None (proper HMAC-based auth for health endpoints) |
| A02:2021 - Cryptographic Failures | None (uses HMAC.compare_digest for timing-safe comparison) |
| A03:2021 - Injection | 1 (SQL interpolation - MITIGATED by identifier validation) |
| A04:2021 - Insecure Design | None |
| A05:2021 - Security Misconfiguration | None |
| A06:2021 - Vulnerable Components | N/A (dependency analysis outside scope) |
| A07:2021 - Identification and Authentication Failures | None |
| A08:2021 - Software and Data Integrity Failures | None |
| A09:2021 - Security Logging and Monitoring Failures | None (proper use of `scrub_nats_url()` to redact secrets in logs) |
| A10:2021 - Server-Side Request Forgery | None |

### Recommendations

**Priority 1 (Medium):**
1. Add path traversal validation to `_read_secret()` in `/home/mickael/projects/lyra/src/lyra/bootstrap/infra/health.py`:
   ```python
   def _read_secret(name: str) -> str:
       # Prevent path traversal
       if "/" in name or "\\" in name or ".." in name:
           raise ValueError(f"Invalid secret name: {name!r}")
   ```

**Priority 2 (Low):**
2. Standardize HMAC comparison pattern across both health endpoints - always encode to bytes before comparison
3. Replace `sys.exit()` calls with custom exceptions (`ConfigurationError`, `BootstrapError`) in library code, handle exit at CLI entry point only
4. Add inline comments documenting the SQL identifier validation approach in `bootstrap_stores.py` for future maintainers

**Positive Security Practices Observed:**
- All secrets loaded from environment variables or files, never hardcoded
- Uses `asyncio.create_subprocess_exec()` with argument list (not shell execution)
- Uses `hmac.compare_digest()` for timing-safe token comparison
- Uses `scrub_nats_url()` to redact sensitive URL components in logs
- Config path validation restricts paths to user home directory
- No use of `pickle`, `marshal`, or unsafe `yaml.load()`
