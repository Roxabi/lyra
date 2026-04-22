# Security Audit: core/hub

### Summary

The core/hub module demonstrates strong security architecture with proper trust level enforcement, rate limiting, and input validation. However, there are two areas of concern: callback execution from message metadata could allow code execution if an attacker controls the metadata source, and logging of user identifiers may expose PII in production logs. No SQL injection, command injection, path traversal vulnerabilities, or hardcoded secrets were found.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| middleware_submit.py | 85-90 | Callback extracted from `platform_meta` and executed without source validation | Medium | Validate callback source or use explicit allowlist for trusted callback types |
| _dispatch.py | 93-96, 170-174 | `_on_dispatched` callback from metadata executed with only `callable()` check | Medium | Verify callback comes from internal code paths, not external input |
| path_validation.py | 113 | `thread_session_id` from `platform_meta` used for session resume | Low | Scope validation is implemented; document trust requirement for adapters |
| middleware.py | 121, 129-130 | User ID and scope ID logged in trace events | Low | Consider redacting PII in production logs or using hashed identifiers |
| middleware_guards.py | 89 | User ID logged when dropping BLOCKED users | Low | Use user hash or anonymized identifier for logging |
| middleware_stt.py | 116-124 | Temp file creation with user-controlled extension from MIME type | Low | MIME type mapping `_MIME_TO_EXT` limits extensions to safe values - acceptable |
| outbound_tts.py | 136, 167 | Use of `assert` for type narrowing | Info | Asserts are for type narrowing after None checks, not validation - acceptable |
| hub_rate_limit.py | 50 | User ID used as rate limit key | Info | Correct per-user rate limiting design |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | 0 - Trust levels properly enforced |
| A02:2021 - Cryptographic Failures | 0 - No secrets stored in code |
| A03:2021 - Injection | 0 - No SQL, command, or path traversal vectors |
| A04:2021 - Insecure Design | 1 - Callback execution pattern needs source validation |
| A05:2021 - Security Misconfiguration | 0 - No hardcoded credentials |
| A06:2021 - Vulnerable Components | N/A - Dependency analysis not in scope |
| A07:2021 - Identification and Authentication Failures | 0 - Authenticator pattern properly implemented |
| A08:2021 - Software and Data Integrity Failures | 1 - Callback execution from metadata |
| A09:2021 - Security Logging and Monitoring Failures | 2 - PII in logs |
| A10:2021 - Server-Side Request Forgery | 0 - No URL fetching from user input |

### Recommendations

**High Priority:**
1. **Callback Source Validation**: Implement an allowlist or signature verification for callbacks extracted from `metadata` and `platform_meta`. The current pattern of extracting `_on_dispatched` and `_session_update_fn` from message metadata and calling them is a potential code execution vector if metadata can be influenced by attackers.

**Medium Priority:**
2. **PII Logging**: Implement PII redaction in production logs. Replace direct user_id logging with hashed or anonymized identifiers. Consider adding a logging sanitizer that redacts user_id, scope_id, and pool_id values.

**Low Priority:**
3. **Documentation**: Add security documentation to `hub_protocol.py` ChannelAdapter docstring emphasizing that adapters must not pass untrusted callback functions in metadata. The docstring already states adapters are responsible for verifying identity (line 27-31), but should explicitly prohibit passing executable callbacks from external sources.

**Positive Security Practices Observed:**
- Trust level enforcement with BLOCKED user dropping (middleware_guards.py:87-99)
- Per-user sliding window rate limiting (hub_rate_limit.py)
- Platform validation rejecting unknown platforms (middleware_guards.py:42-61)
- STT transcript length validation with MAX_TRANSCRIPT_LEN (middleware_stt.py:31, 172-176)
- Slash-command injection guard in STT (middleware_stt.py:166-169)
- Secure temp file handling with tempfile.mkstemp (middleware_stt.py:116)
- Session scope validation against TurnStore (path_validation.py:126-134)
