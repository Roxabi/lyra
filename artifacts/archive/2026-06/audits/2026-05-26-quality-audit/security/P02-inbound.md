### Summary

- **Dispatcher** crashes with unhandled `KeyError` when `msg.platform` is unknown (e.g. CLI or future adapter), causing message loss / implicit DoS.
- **SessionBuilder** silently misroutes unknown platforms to `TELEGRAM`, breaking pool isolation.
- **Router** enforces access-control rules (DM/mention/owned-thread/watch-channel) but never logs `DROP` reasons, leaving an OWASP A09 gap at the trust boundary.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/inbound/dispatcher.py` | 58 | **Medium** | `Platform[msg.platform.upper()]` raises `KeyError` for any platform string not in the enum (e.g. `"cli"`, `"webhook"`, or a spoofed/broken adapter). The exception propagates out of `dispatch()` and the inbound pipeline aborts, dropping the message without backpressure. | Wrap the enum lookup in `try/except ValueError`; return `RouteDecision.DROP` semantics (or raise a typed pipeline exception that the adapter can translate into a backpressure ack). Mirror the pattern already used in `_platform_enum` in `session_builder.py`. |
| `src/lyra/inbound/session_builder.py` | 226-238 | **Low** | `_platform_enum()` logs a warning but **defaults to `Platform.TELEGRAM`** for unknown strings. This pools the message with Telegram traffic, violating platform isolation and potentially leaking cross-platform session state. | Change fallback to `None` and skip session persistence / drop the message. If a default is required for forward-compatibility, route to a dead-letter pool (`platform="unknown"`) instead of `TELEGRAM`. |
| `src/lyra/inbound/router.py` | 47-88 | **Low** | `Router.decide` returns `DROP` for four distinct security reasons (unknown platform, non-mention in group, non-owned thread, non-watch channel) but emits **zero log lines**. Security auditing, incident response, and bot-abuse detection are impossible without DROP telemetry. | Add a single `log.debug` (or `log.info` at a gated rate) inside `decide` that records `platform`, `bot_id`, `scope_id`, and the drop reason code before returning `DROP`. Keep it concise — do not log message text. |
| `src/lyra/inbound/session_builder.py` | 96-97, 171-174, 213-216 | **Info** | `log.exception` on SQLite errors includes `pool_id` and `thread_id`. These strings contain `bot_id` and scope/channel/thread identifiers. While not secrets, they are operational metadata that aid reconnaissance if logs are exfiltrated. | Strip scope identifiers from exception logs; log only a hash or a redacted prefix (e.g. `pool_id=telegram:<bot>:<hash>`). Alternatively, move full identifiers to `extra` behind a debug-level formatter that is disabled in production. |

### Metrics

| Metric | Value |
|--------|-------|
| Files analyzed | 9 |
| Total lines | 707 |
| Findings | 4 |
| Severity breakdown | Medium: 1 / Low: 2 / Info: 1 |
| OWASP Top 10 coverage in partition | A01 Injection: 0 (all SQL uses `?` params) <br> A05 Broken Access Control: 1 (router silent drops) <br> A07 Authentication: 0 (handled upstream in adapters) <br> A09 Insufficient Logging: 2 (router + dispatcher) <br> Others: 0 |
| Credential handling (hardcoded tokens / env fallbacks) | 0 findings in partition |
| Path traversal in file operations | 0 findings in partition |
| NATS subject injection | 0 (subjects are hardcoded constants) |

### Recommendations (prioritized)

1. **Harden `Dispatcher.dispatch` against unknown platforms** — Add a `try/except ValueError` around `Platform[msg.platform.upper()]` in `dispatcher.py:58`. If the lookup fails, invoke `on_drop`, send a generic backpressure message, and return cleanly. This fixes the only Medium-severity item.
2. **Remove TELEGRAM fallback in `_platform_enum`** — Change the fallback in `session_builder.py` to `None` (and handle `None` in the caller) or to a dedicated sentinel. Defaulting to an active platform pool is an isolation bug.
3. **Add DROP telemetry to `Router.decide`** — Instrument each `return RouteDecision.DROP` path with a single structured log line (`platform`, `bot_id`, `reason_code`). This is the most impactful Low-severity fix for observability and incident response.
4. **Redact operational identifiers from `SessionBuilder` exception logs** — Replace raw `pool_id` / `thread_id` in `log.exception` calls with a truncated hash or redacted form to reduce reconnaissance surface.
5. **(Out-of-scope note)** The GH token dispenser (`tools/gh_token/`) and NATS ACL configuration are outside the `src/lyra/inbound/` partition; they were not consumed by any inbound file and therefore not audited here. If needed, schedule a separate P05 audit for `tools/` and `deploy/quadlet/`.
