# Security Audit: Adapters

### Summary

The adapters codebase demonstrates strong security practices overall, with proper credential handling via environment variables, HMAC-protected webhook authentication for Telegram, and path traversal protection for file operations. However, there are minor concerns around assertion usage that could be bypassed, and some areas could benefit from additional input validation.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| discord_outbound.py | 61 | Use of `assert` statement for channel validation | Low | Replace `assert channel is not None` with explicit `if channel is None: raise RuntimeError(...)` |
| shared/_shared_streaming_emitter.py | 285 | Use of `assert` after None check | Low | Convert to explicit validation with proper exception |
| telegram/telegram.py | 112-124 | Temp directory path from environment variable without canonicalization | Low | Consider using `Path.resolve()` to prevent symlink attacks |
| nats/nats_envelope_handlers.py | 195 | JSON parsing of untrusted NATS message data | Medium | Already wrapped in try-except; document trust boundary |
| shared/cli.py | 23-47 | CLI adapter grants OWNER trust to all local input | Info | By design for local CLI - document trust assumption |
| discord/discord_formatting.py | 49-59 | Thread name derived from user content without sanitization | Low | Consider additional sanitization for special characters |
| telegram/telegram_audio.py | 62-64 | Temp file created with predictable suffix `.ogg` | Info | Using `tempfile.mkstemp()` is secure; suffix is not a vulnerability |

### Positive Security Controls Identified

| File | Line | Control | Description |
|------|------|---------|-------------|
| discord_config.py | 15,22 | Token protection | `token: str = Field(repr=False)` prevents accidental logging |
| telegram/telegram.py | 74-82 | HMAC verification | Timing-safe comparison via `hmac.compare_digest()` for webhook secret |
| shared/_shared_text.py | 37-53 | Path traversal protection | `sanitize_filename()` strips path components, controls chars, validates extensions |
| telegram_audio.py | 52-74 | File size validation | Pre/post download size checks against `LYRA_MAX_AUDIO_BYTES` |
| telegram/telegram.py | 79 | Auth failure handling | Returns HTTP 401 without revealing secret details |
| shared/_shared_text.py | 40 | Null byte stripping | Removes `\x00-\x1f\x7f` control characters from filenames |
| shared/_shared_text.py | 48-51 | Extension whitelist | Filename extension validated against `allowed_exts` frozenset |
| nats/nats_outbound_listener.py | 50 | Token validation | `validate_nats_token()` call for queue group |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | None - proper HMAC auth on Telegram webhooks |
| A02:2021 - Cryptographic Failures | None - using timing-safe HMAC comparison |
| A03:2021 - Injection | None - no SQL/command injection vectors found |
| A04:2021 - Insecure Design | None - trust boundaries properly documented |
| A05:2021 - Security Misconfiguration | 2 Low - assertions used for validation |
| A06:2021 - Vulnerable Components | Not analyzed (dependency scan needed) |
| A07:2021 - Identification and Authentication | None - platform auth delegated to Discord/Telegram SDKs |
| A08:2021 - Software and Data Integrity Failures | 1 Low - temp dir path from env var |
| A09:2021 - Security Logging and Monitoring Failures | None - sensitive data excluded from logs |
| A10:2021 - Server-Side Request Forgery | None - no SSRF vectors found |

### Recommendations

1. **High Priority**: None identified

2. **Medium Priority**:
   - Add explicit documentation about the trust boundary at the NATS layer in `nats_envelope_handlers.py`
   - Consider adding a schema validation step for inbound NATS envelopes before deserialization

3. **Low Priority**:
   - Replace `assert` statements with explicit validation in `discord_outbound.py:61` and `shared/_shared_streaming_emitter.py:285`
   - Use `Path.resolve()` on `LYRA_AUDIO_TMP` environment variable to canonicalize path before use
   - Consider adding content sanitization for thread names derived from user messages

4. **Best Practice Enhancements**:
   - Document the security contract more prominently in adapter docstrings
   - Consider adding request ID tracing for security audit logs
   - Add rate limiting metrics for monitoring potential abuse patterns
