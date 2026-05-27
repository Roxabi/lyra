### Summary

- P03 core (`src/lyra/outbound/`, `src/lyra/streaming/`) is clean: no secrets, no file I/O, no untrusted deserialization. Exception handling consistently uses `type(exc).__name__` — no sensitive-data leakage via `str(exc)`.
- GH token dispenser has two env-var validation gaps: `LYRA_GH_RATE_LIMIT_S` accepts 0/negative values (bypasses abuse floor) and `LYRA_GH_PEM_PATH` follows symlinks without ownership/permission verification.
- NATS ACLs for outbound subjects remain sound; the known `$JS.API.>` over-permission is documented in `acl-matrix.json` (#1293) and has not regressed.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/tools/gh_token/daemon.py` | 98 | Medium | `LYRA_GH_RATE_LIMIT_S` env var cast to `float` without lower-bound validation. A value of `0` or negative disables the `RateLimiter` abuse floor, allowing pathological mint churn. | Add validation in `_load_config()`: `if config.rate_limit_s <= 0: raise DaemonConfigError(...)` |
| `src/lyra/tools/gh_token/daemon.py` | 88-90 | Low | `LYRA_GH_PEM_PATH` only checks `Path.is_file()`. A symlink attack could redirect to an attacker-readable PEM. Ownership and mode (0o400 helper-owned) are not verified. | Add `os.lstat()` check: reject symlinks, verify `st_uid` matches process uid and mode is `0o400` or tighter |
| `src/lyra/adapters/discord/discord_outbound.py` | 124 | Low | `log.warning(..., exc)` logs `str(exc)` for `discord.HTTPException`. Discord API error strings may contain message content or rate-limit metadata. | Sanitize to `type(exc).__name__` only, consistent with `OutboundErrorHandler` discipline |
| `src/lyra/adapters/nats/nats_outbound_listener.py` | 166 | Low | Broad `except Exception` around `adapter.send_streaming()` uses `log.exception()`, writing full traceback to logs. A platform SDK exception could carry API tokens or message content in its args. | Use `log.warning("send_streaming failed: %s", type(exc).__name__)` or apply a log-scrubbing filter on adapter-boundary exceptions |
| `src/lyra/adapters/telegram/telegram_outbound.py` | 248 | Low | `log.debug("Placeholder text edit skipped: %s", exc)` logs `str(exc)` for `TelegramAPIError` at DEBUG level. If DEBUG logs are collected centrally, this leaks Telegram API error details. | Sanitize to `type(exc).__name__` only |

### Metrics

| Metric | Value |
|--------|-------|
| Files in P03 | 9 (8 `.py` + `__init__.py`) |
| Total findings | 5 |
| High severity | 0 |
| Medium severity | 1 |
| Low severity | 4 |
| OWASP A01 (Broken Access Control) | 1 (PEM path symlink) |
| OWASP A03 (Injection) | 0 in P03 core; Telegram MarkdownV2 escaping is delegated to external dependency `telegramify_markdown` |
| OWASP A05 (Security Misconfiguration / Sensitive Data Exposure) | 4 (rate-limit bypass, PEM symlink, Discord log leak, NATS listener log leak) |
| OWASP A07 (Authentication) | 0 |
| OWASP A08 (Software/Data Integrity) | 0 |
| OWASP A10 (Insufficient Logging) | 0 — failures are logged; no audit-grade security events needed in this partition |

### Recommendations (prioritized)

1. **Harden GH token dispenser env validation** — Add lower-bound check on `LYRA_GH_RATE_LIMIT_S` and symlink/ownership/mode verification on `LYRA_GH_PEM_PATH` before constructing `JWTSigner`. This is the only medium-severity item and the quickest fix.
2. **Enforce `type(exc).__name__` logging discipline on adapter boundaries** — Apply the same scrub rule used in `OutboundErrorHandler` to all `log.warning(..., exc)` and `log.exception()` calls inside `discord_outbound.py`, `telegram_outbound.py`, and `nats_outbound_listener.py` that wrap platform or adapter exceptions.
3. **Add CI gate for exception-string leakage in adapter outbound code** — A linter or grep-based test that fails on `log.*%(.*exc\b)` or `log.exception` inside broad `except Exception` blocks in `src/lyra/adapters/**/`*`outbound.py`.
4. **Pin and audit `telegramify_markdown` dependency** — The Telegram outbound path relies on this external library for MarkdownV2 escaping. Add a regression test that verifies both the primary library and the built-in fallback regex escape all Telegram MarkdownV2 metacharacters (`_*[]()~`>#+=|{}.!\`) before `send_message`/`edit_message_text` calls with `parse_mode="MarkdownV2"`.
5. **Continue tracking NATS ACL hardening (#1293)** — No new gap found in outbound subjects. The documented `$JS.API.>` over-permission in adapters/workers remains the correct priority for future hardening, per `acl-matrix.json` notes.
