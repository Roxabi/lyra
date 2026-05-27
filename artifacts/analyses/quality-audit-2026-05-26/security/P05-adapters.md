### Summary
- No critical or high-severity findings; all gaps are Medium (2) or Low (5). The adapter partition has solid defense-in-depth (HMAC webhook verifier, path-traversal guard on `switch_cwd`, magic-byte audio gating, sanitized exception bus messages).
- Main residual risks are configuration-time bypasses (`LYRA_MAX_AUDIO_BYTES` negative values) and operational data exposure (Telegram token in object repr, clipool heartbeat pool size).
- OWASP coverage is strong on injection, broken auth, and SSRF; weakest on security logging/monitoring (A09) and security misconfiguration edge cases (A05).

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/adapters/telegram/telegram.py` | 122 | Medium | `LYRA_MAX_AUDIO_BYTES` is cast with `int()` but never range-validated. A negative value bypasses both pre-download and post-download size guards, allowing arbitrary-size audio uploads → memory/disk exhaustion. Same pattern exists in `discord/adapter.py:111` and `shared/_shared_audio.py:39`. | Add validation: assert value is int and `1024 <= max_bytes <= 200_000_000` (200 MB hard ceiling). Fail fast on startup with a clear error. |
| `src/lyra/adapters/telegram/telegram.py` | 96 | Medium | `self._token` stores the Telegram bot token as a plain string. `TelegramAdapter` lacks a `__repr__` override, so accidental `repr(adapter)`, crash dumps, or debug endpoints may leak the credential. | Mask the token in `__repr__` (e.g., `<TelegramAdapter bot_id=... token=***>`) or store it in a private slot that overrides `__getstate__` to exclude it from serialization. |
| `src/lyra/adapters/nats/nats_outbound_listener.py` | 58 | Low | NATS subject is built via f-string: `f"lyra.outbound.{platform.value}.{bot_id}"`. `bot_id` comes from config but is not validated against NATS wildcard characters (`*`, `>`, `.`). A misconfigured bot_id containing wildcards would broaden subscription/publish scope. | Validate `bot_id` with `re.match(r'^[a-zA-Z0-9_-]+$')` before subject construction; raise `ValueError` on mismatch. |
| `src/lyra/adapters/nats/mint_failure_subscriber.py` | 47 | Low | Same NATS subject wildcard risk as above: `ops_telegram_bot_id` is interpolated into `lyra.outbound.telegram.{ops_telegram_bot_id}` without sanitizing NATS meta-characters. | Apply the same `bot_id` validation rule to `ops_telegram_bot_id` at construction time. |
| `src/lyra/adapters/telegram/telegram_audio.py` | 63 | Low | `tempfile.mkstemp` returns `(fd, path)`; the fd is discarded (`_`) and never closed. Under sustained audio load this leaks file descriptors, eventually exhausting the process fd limit (DoS). | Capture and close the fd: `fd, tmp_str = tempfile.mkstemp(...); os.close(fd)`. |
| `src/lyra/tools/gh_token/helper.py` | 87 | Low | `JWTSigner` calls `serialization.load_pem_private_key(raw, password=None)`, which rejects password-protected PEMs. This forces operators to store the private key unencrypted at rest. | Support an optional `LYRA_GH_PEM_PASSWORD` env var; pass it as `password=password.encode()` when present, preserving backward compatibility for unencrypted PEMs. |
| `src/lyra/adapters/clipool/clipool_worker.py` | 156 | Low | `heartbeat_payload` exposes `len(self._pool._entries)` over the NATS heartbeat subject. While the subject is restricted, this leaks internal operational state (pool size) without an opt-out. | Remove `pool_count` from the default heartbeat payload or gate it behind an explicit `CLIPOOL_HEARTBEAT_VERBOSE=1` flag. |

### Metrics

| Metric | Value |
|--------|-------|
| Files audited | 35 |
| Total LOC | 6,814 |
| OWASP Top 10 categories with explicit adapter-side defenses | 8 / 10 (80 %) |
| Findings by severity | Medium: 2, Low: 5 |
| Files with security-relevant code (auth, credential, file I/O, NATS) | 20 / 35 (57 %) |
| Hardcoded secrets/tokens detected | 0 |
| Path-traversal guard present (`switch_cwd`, `sanitize_filename`) | 2 / 2 (100 %) |

### Recommendations (prioritized)

1. **Add `LYRA_MAX_AUDIO_BYTES` range validation** — The most exploitable gap. A single misconfigured env var bypasses all audio size limits. Implement a `validate_audio_bytes_limit()` helper used by both Telegram and Discord adapters on startup.

2. **Mask Telegram bot token in object representation** — Credentials should never be reachable via `repr()` or serialization. A one-line `__repr__` change prevents accidental leakage in logging, exception reporting, or observability pipelines.

3. **Sanitize `bot_id` before NATS subject interpolation** — NATS wildcards in subjects are a classic cross-tenant scope leak vector. A regex guard at construction time is cheap and eliminates the risk.

4. **Fix `mkstemp` fd leak in Telegram audio download** — A resource-exhaustion DoS vector under load. The fix is a single `os.close(fd)` call. Backport to any other tmp file creation paths in the adapter tree.

5. **Add structured security event logging** — Path-traversal rejections (`switch_cwd`), webhook auth failures, and blocked-user events are logged as unstructured warnings. Introduce a `log_security_event(event_type, user_id, details)` helper (or use `extra=` with a fixed schema) so these events can be ingested by SIEM / alerting without regex parsing.
