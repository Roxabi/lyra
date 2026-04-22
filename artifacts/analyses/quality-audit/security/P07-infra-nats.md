# Security Analysis: Infrastructure + NATS

### Summary
The infrastructure and NATS modules demonstrate strong security practices overall, with all SQL operations using parameterized queries, encrypted credential storage via Fernet, and robust input validation for NATS subjects. However, a potential path traversal vulnerability exists in the STT client where file paths are resolved without boundary validation, and the worker registry could allow limited resource exhaustion during attack windows.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py | 152 | Path Traversal: `Path(path).resolve()` called on user-supplied path without validation - allows reading arbitrary files if path is attacker-controlled | Medium | Validate path is within expected directory boundary before reading |
| /home/mickael/projects/lyra/src/lyra/nats/voice_health.py | 86-92 | Registry DoS: MAX_WORKERS=64 cap logs warning per incident but attacker can flood registry during 15s heartbeat window | Low | Consider rate-limiting new worker registrations or using sliding window |
| /home/mickael/projects/lyra/src/lyra/infrastructure/stores/credential_store.py | 56-67 | Key file read: Error messages could leak existence of key file path | Low | Ensure KeyringError messages don't expose paths in production logs |
| /home/mickael/projects/lyra/src/lyra/nats/nats_bus.py | 236-237 | JSON parse exception logs platform/bot_id but not the malformed payload content | Info | Good practice - malformed payloads are not logged, preventing log injection |
| /home/mickael/projects/lyra/src/lyra/infrastructure/stores/identity_alias_store.py | 224-225 | Challenge code generation uses `secrets.choice` (cryptographically secure) | Good | N/A - Positive finding |
| /home/mickael/projects/lyra/src/lyra/infrastructure/stores/sqlite_base.py | 88-89 | Good: WAL mode and busy_timeout configured appropriately | Good | N/A - Positive finding |

### OWASP Coverage

| Category | Issues Found |
|----------|--------------|
| A01:2021 - Broken Access Control | 0 - Trust levels enforced via AuthStore |
| A02:2021 - Cryptographic Failures | 0 - Fernet encryption used for credentials, 0o600 key file permissions |
| A03:2021 - Injection | 0 - All SQL uses parameterized queries (? placeholders) |
| A04:2021 - Insecure Design | 1 - Path resolution without boundary check |
| A05:2021 - Security Misconfiguration | 0 - WAL mode, foreign keys enabled |
| A06:2021 - Vulnerable Components | N/A - Dependency scan required |
| A07:2021 - Auth Failures | 0 - Challenge codes use SHA256, TOCTOU race prevented with BEGIN IMMEDIATE |
| A08:2021 - Software Integrity | 0 - No pickle/yaml unsafe deserialization |
| A09:2021 - Logging Failures | 0 - Sensitive data not logged (tokens, passwords excluded) |
| A10:2021 - SSRF | 0 - No external URL fetching in analyzed modules |

### Recommendations

1. **High Priority**: Add path validation in `NatsSttClient.transcribe()` to ensure resolved path stays within expected boundaries (e.g., temp directory or designated audio storage). Consider using `resolved.is_relative_to(allowed_base)` or equivalent check.

2. **Medium Priority**: Implement rate-limiting for new worker registrations in `VoiceWorkerRegistry` to prevent registry flooding attacks during heartbeat windows.

3. **Low Priority**: Review error message content in `LyraKeyring` to ensure file paths are not exposed in production logs (currently they are included in KeyringError messages).

### Positive Security Patterns Observed

- **Parameterized SQL**: All database operations use `?` placeholders consistently
- **Encryption at Rest**: Bot tokens encrypted with Fernet before SQLite storage
- **Secure Random**: `secrets.choice()` used for challenge code generation
- **Input Validation**: NATS subject tokens validated against injection patterns
- **Circuit Breaker**: NATS clients implement circuit breaker for resilience
- **Transaction Safety**: `BEGIN IMMEDIATE` prevents TOCTOU races in challenge validation
- **No Unsafe Deserialization**: JSON parsing only, no pickle/marshal/yaml usage
- **Secure File Permissions**: Key files created with 0o600 permissions atomically
